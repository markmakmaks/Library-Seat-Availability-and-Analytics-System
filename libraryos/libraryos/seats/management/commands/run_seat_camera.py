"""
Camera-based seat detection (OpenCV + YOLOv8).

    python manage.py run_seat_camera --calibrate            # draw each seat's ROI, saved to the database
    python manage.py run_seat_camera                        # detect, with a preview window
    python manage.py run_seat_camera --source rtsp://... --no-preview --skip-frames 15

A seat is triggered by a person (COCO class 0) when intersection-area / seat-ROI-area reaches
--overlap-threshold, or when the person's upper-body point is inside the ROI. Belongings (backpack 24,
handbag 26, laptop 63) use the same overlap test. A per-seat score (+3 per hit, -1 per miss, 0..12)
smooths the readings before they reach services.sync_seat_from_camera.

Video is read and shown every frame; YOLO runs only every --skip-frames frames and its boxes are reused
in between. The database is only written when a seat's reading changes (plus a slow heartbeat so the
time-based rules in the service, such as no-show grace and unattended belongings, still advance).
"""
import time

from django.core.management.base import BaseCommand, CommandError
from django.db import close_old_connections

from seats import services
from seats.models import Seat

CAPTURE_WIDTH, CAPTURE_HEIGHT = 1280, 720
HEARTBEAT_SECONDS = 30      # re-send an unchanged reading this often
STATUS_REFRESH_SECONDS = 5  # how often to re-read seat statuses for the preview
PERSON = 0
BELONGINGS = {24: "backpack", 26: "handbag", 63: "laptop"}

# BGR colours
GREEN, RED, ORANGE, GREY, WHITE = (0, 200, 0), (0, 0, 220), (0, 165, 255), (150, 150, 150), (255, 255, 255)
STATUS_COLOURS = {"available": GREEN, "pending": (0, 215, 255), "occupied": RED, "reserved": ORANGE, "maintenance": GREY}


def shrink_box(box, shrink):
    """Pull a detection inward on every side so wide boxes don't bleed into neighbouring seats."""
    x1, y1, x2, y2 = box
    dx, dy = (x2 - x1) * shrink, (y2 - y1) * shrink
    return (x1 + dx, y1 + dy, x2 - dx, y2 - dy)


def pick_device():
    """cuda, then Apple mps, else cpu."""
    try:
        import torch
    except ImportError:
        return "cpu"
    if torch.cuda.is_available():
        return "cuda"
    mps = getattr(torch.backends, "mps", None)
    if mps is not None and mps.is_available():
        return "mps"
    return "cpu"


def overlap_ratio(box, roi):
    """Intersection area of the detection and the seat ROI, divided by the seat ROI area."""
    bx1, by1, bx2, by2 = box
    rx1, ry1, rx2, ry2 = roi
    iw = min(bx2, rx2) - max(bx1, rx1)
    ih = min(by2, ry2) - max(by1, ry1)
    seat_area = (rx2 - rx1) * (ry2 - ry1)
    if iw <= 0 or ih <= 0 or seat_area <= 0:
        return 0.0
    return iw * ih / seat_area


def point_in_roi(x, y, roi):
    return roi[0] <= x <= roi[2] and roi[1] <= y <= roi[3]


def person_hit(box, roi, threshold):
    """(triggered, overlap) for a person box: enough overlap, or the upper-body point inside the ROI."""
    x1, y1, x2, y2 = box
    overlap = overlap_ratio(box, roi)
    upper_body = ((x1 + x2) / 2, y1 + 0.35 * (y2 - y1))
    return overlap >= threshold or point_in_roi(*upper_body, roi), overlap


def object_hit(box, roi, threshold):
    x1, y1, x2, y2 = box
    return overlap_ratio(box, roi) >= threshold or point_in_roi((x1 + x2) / 2, (y1 + y2) / 2, roi)


class ScoreDebouncer:
    """
    Asymmetric anti-flicker counter, 0..MAX. +3 on a detection, -1 on a miss, so a seat fills within
    two checks but survives brief dropouts (bending down, sitting very still).
    state: None until the score first settles, then True (>= ON) / False (== 0), with hysteresis between.
    """

    MAX, ON, UP, DOWN, START = 12, 6, 3, 1, 5

    def __init__(self):
        self.score = self.START
        self.state = None

    def update(self, present):
        self.score = min(self.MAX, self.score + self.UP) if present else max(0, self.score - self.DOWN)
        if self.score >= self.ON:
            self.state = True
        elif self.score == 0:
            self.state = False
        return self.state


class Command(BaseCommand):
    help = "Detect seat occupancy from a camera (YOLOv8) and sync it with reservations."

    def add_arguments(self, parser):
        parser.add_argument("--source", default="0", help="Webcam index, RTSP URL or video file (default 0)")
        parser.add_argument("--skip-frames", type=int, default=10, help="Run YOLO on every Nth video frame")
        parser.add_argument("--model", default="yolov8n.pt", help="YOLOv8 weights (default yolov8n.pt; try yolov8s.pt on a GPU)")
        parser.add_argument("--conf", type=float, default=0.25, help="Detection confidence threshold")
        parser.add_argument("--nms-iou", type=float, default=0.45, help="YOLO non-max-suppression IoU")
        parser.add_argument("--imgsz", type=int, default=640, help="YOLO inference size")
        parser.add_argument("--box-shrink", type=float, default=0.15,
                            help="Shrink every detection inward by this fraction per side (default 0.15)")
        parser.add_argument("--overlap-threshold", type=float, default=0.18,
                            help="Min (box intersect seat) / seat area to trigger a seat")
        parser.add_argument("--no-preview", action="store_true", help="Run headless (no OpenCV window)")
        parser.add_argument("--calibrate", action="store_true", help="Draw and save seat ROIs instead of detecting")

    # ----------------------------------------------------------------- setup
    def handle(self, *args, **opts):
        try:
            import cv2
        except ImportError:
            raise CommandError("Missing dependencies. Run: pip install -r requirements.txt")
        self.cv2 = cv2

        source = int(opts["source"]) if str(opts["source"]).isdigit() else opts["source"]
        cap = cv2.VideoCapture(source)
        if not cap.isOpened():
            raise CommandError(f"Could not open video source {opts['source']!r}.")
        cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)  # always the newest frame, no stale backlog
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, CAPTURE_WIDTH)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, CAPTURE_HEIGHT)
        width, height = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)), int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        self.stdout.write(f"Capture resolution: {width}x{height}")
        if (width, height) != (CAPTURE_WIDTH, CAPTURE_HEIGHT):
            self.stderr.write(f"Warning: the source did not accept {CAPTURE_WIDTH}x{CAPTURE_HEIGHT}. ROIs are stored in this resolution's pixels.")
        try:
            if opts["calibrate"]:
                self.calibrate(cap)
            else:
                self.detect(cap, opts)
        except KeyboardInterrupt:
            pass
        finally:
            cap.release()
            cv2.destroyAllWindows()

    # ------------------------------------------------------------- calibrate
    def calibrate(self, cap):
        cv2 = self.cv2
        seats = list(Seat.objects.select_related("zone"))
        if not seats:
            raise CommandError("No seats in the database. Run: python manage.py seed_seats")
        idx = 0
        drag = {"start": None, "end": None, "down": False}

        def on_mouse(event, x, y, flags, param):
            if event == cv2.EVENT_LBUTTONDOWN:
                drag.update(start=(x, y), end=(x, y), down=True)
            elif event == cv2.EVENT_MOUSEMOVE and drag["down"]:
                drag["end"] = (x, y)
            elif event == cv2.EVENT_LBUTTONUP:
                drag.update(end=(x, y), down=False)

        win = "Calibrate seat ROIs"
        cv2.namedWindow(win, cv2.WINDOW_NORMAL)
        cv2.resizeWindow(win, 1280, 720)
        cv2.setMouseCallback(win, on_mouse)
        self.stdout.write("Drag a box over the selected seat. s=save  c=clear  n/p=next/previous seat  q=quit")

        while True:
            ok, frame = cap.read()
            if not ok:
                raise CommandError("Lost the video source.")
            seat = seats[idx]
            for other in seats:
                if other.has_roi:
                    colour = ORANGE if other is seat else GREY
                    cv2.rectangle(frame, other.roi[:2], other.roi[2:], colour, 1)
                    cv2.putText(frame, other.seat_id, (other.roi_x1 + 3, other.roi_y1 + 14), cv2.FONT_HERSHEY_SIMPLEX, 0.45, colour, 1)
            if drag["start"] and drag["end"]:
                cv2.rectangle(frame, drag["start"], drag["end"], GREEN, 2)
            label = f"Seat {seat.seat_id} ({idx + 1}/{len(seats)}) ROI: {seat.roi or 'not set'}  [s]ave [c]lear [n]ext [p]rev [q]uit"
            cv2.putText(frame, label, (10, 22), cv2.FONT_HERSHEY_SIMPLEX, 0.55, WHITE, 2)
            cv2.imshow(win, frame)

            key = cv2.waitKey(30) & 0xFF
            if key == ord("q"):
                return
            if key in (ord("n"), ord("p")):
                idx = (idx + (1 if key == ord("n") else -1)) % len(seats)
                drag.update(start=None, end=None, down=False)
            elif key == ord("c"):
                seat.roi_x1 = seat.roi_y1 = seat.roi_x2 = seat.roi_y2 = None
                seat.save(update_fields=["roi_x1", "roi_y1", "roi_x2", "roi_y2"])
                self.stdout.write(f"Cleared ROI for {seat.seat_id}")
            elif key == ord("s") and drag["start"] and drag["end"] and not drag["down"]:
                (x1, y1), (x2, y2) = drag["start"], drag["end"]
                h, w = frame.shape[:2]
                x1, x2 = sorted((max(0, min(w, x1)), max(0, min(w, x2))))
                y1, y2 = sorted((max(0, min(h, y1)), max(0, min(h, y2))))
                if x2 - x1 < 10 or y2 - y1 < 10:
                    self.stdout.write("Box too small - drag a bigger area.")
                    continue
                seat.roi_x1, seat.roi_y1, seat.roi_x2, seat.roi_y2 = x1, y1, x2, y2
                seat.save(update_fields=["roi_x1", "roi_y1", "roi_x2", "roi_y2"])
                self.stdout.write(f"Saved {seat.seat_id}: ({x1}, {y1}) - ({x2}, {y2})")
                drag.update(start=None, end=None)
                idx = (idx + 1) % len(seats)

    # ---------------------------------------------------------------- detect
    def detect(self, cap, opts):
        cv2 = self.cv2
        try:
            from ultralytics import YOLO
        except ImportError:
            raise CommandError("Missing dependencies. Run: pip install -r requirements.txt")
        model = YOLO(opts["model"])
        device = pick_device()
        wanted = [PERSON, *BELONGINGS]

        seats = [s for s in Seat.objects.all() if s.has_roi]
        if not seats:
            raise CommandError("No seats have an ROI yet. Run: python manage.py run_seat_camera --calibrate")
        scores = {s.seat_id: ScoreDebouncer() for s in seats}
        debug = {}                      # seat_id -> best person overlap, for the preview
        sent = {}                       # seat_id -> ((occupied, belongings), monotonic time) last written to the DB
        statuses = {s.seat_id: s.status for s in seats}
        statuses_at = time.monotonic()
        boxes = []                      # last (shrunk) detections, redrawn on the frames in between
        frame_no = 0
        skip = max(1, opts["skip_frames"])
        preview = not opts["no_preview"]
        threshold = opts["overlap_threshold"]
        if preview:
            cv2.namedWindow("LibraryOS seat camera", cv2.WINDOW_NORMAL)
            cv2.resizeWindow("LibraryOS seat camera", 1280, 720)
        self.stdout.write(
            f"Watching {len(seats)} seats with {opts['model']} on {device}, YOLO every {skip} frames. "
            f"Ctrl+C{' or q in the window' if preview else ''} to stop."
        )

        while True:
            ok, frame = cap.read()
            if not ok:
                self.stderr.write("Video source dropped; stopping.")
                return
            now = time.monotonic()

            if frame_no % skip == 0:
                result = model.predict(
                    frame, imgsz=opts["imgsz"], conf=opts["conf"], iou=opts["nms_iou"], classes=wanted,
                    device=device, half=(device == "cuda"), verbose=False,
                )[0]
                boxes = [
                    (shrink_box(tuple(float(v) for v in b.xyxy[0]), opts["box_shrink"]), int(b.cls[0]))
                    for b in result.boxes
                ]
                close_old_connections()
                changed_any = False
                for seat in seats:
                    person, objects, best = False, False, 0.0
                    for box, cls in boxes:
                        if cls == PERSON:
                            hit, overlap = person_hit(box, seat.roi, threshold)
                            person, best = person or hit, max(best, overlap)
                        elif object_hit(box, seat.roi, threshold):
                            objects = True
                    debug[seat.seat_id] = best
                    stable = scores[seat.seat_id].update(person)
                    if stable is None:  # wait for the first settled reading after start-up
                        continue
                    reading = (stable, objects)
                    last = sent.get(seat.seat_id)
                    if last and last[0] == reading and now - last[1] < HEARTBEAT_SECONDS:
                        continue        # nothing new: no DB write
                    sent[seat.seat_id] = (reading, now)
                    change = services.sync_seat_from_camera(seat.seat_id, stable, objects)
                    if change:
                        changed_any = True
                        self.stdout.write(f"{seat.seat_id}: {change}")
                if changed_any or now - statuses_at >= STATUS_REFRESH_SECONDS:
                    statuses = {s.seat_id: s.status for s in Seat.objects.filter(seat_id__in=scores)}
                    statuses_at = now
            frame_no += 1

            if preview:
                for seat in seats:
                    colour = STATUS_COLOURS.get(statuses.get(seat.seat_id, "available"), GREEN)
                    cv2.rectangle(frame, seat.roi[:2], seat.roi[2:], colour, 1)
                    cv2.putText(frame, f"{seat.seat_id} {statuses.get(seat.seat_id, '')}", (seat.roi_x1 + 3, seat.roi_y1 + 14),
                                cv2.FONT_HERSHEY_SIMPLEX, 0.45, colour, 1, cv2.LINE_AA)
                    debounce = scores[seat.seat_id]
                    cv2.putText(frame, f"ov {debug.get(seat.seat_id, 0.0) * 100:.0f}%  score {debounce.score}/{debounce.MAX}",
                                (seat.roi_x1 + 3, seat.roi_y2 - 6), cv2.FONT_HERSHEY_SIMPLEX, 0.45, colour, 1, cv2.LINE_AA)
                for (x1, y1, x2, y2), cls in boxes:
                    cv2.rectangle(frame, (int(x1), int(y1)), (int(x2), int(y2)), WHITE, 1)
                    cv2.putText(frame, "person" if cls == PERSON else BELONGINGS[cls], (int(x1), int(y1) - 4),
                                cv2.FONT_HERSHEY_SIMPLEX, 0.45, WHITE, 1, cv2.LINE_AA)
                cv2.imshow("LibraryOS seat camera", frame)
                if cv2.waitKey(1) & 0xFF == ord("q"):
                    return
