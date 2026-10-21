from django import forms
from django.contrib.auth.models import User
from django.contrib.auth.password_validation import validate_password

from .models import Profile

INPUT = "w-full px-3 py-2 border border-slate-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-royal-600"


class SignUpForm(forms.Form):
    ROLE_CHOICES = [(Profile.Role.STUDENT, "Student"), (Profile.Role.PROFESSOR, "Professor")]

    role = forms.ChoiceField(choices=ROLE_CHOICES)
    last_name = forms.CharField(max_length=80)
    first_name = forms.CharField(max_length=80)
    middle_name = forms.CharField(max_length=80, required=False)
    email = forms.EmailField()
    id_number = forms.CharField(max_length=30, label="ID number")
    year_level = forms.ChoiceField(choices=[("", "Select year level")] + Profile.YearLevel.choices, required=False)
    course = forms.CharField(max_length=120, required=False)
    department = forms.CharField(max_length=120, required=False)
    username = forms.CharField(max_length=150)
    password1 = forms.CharField(widget=forms.PasswordInput, label="Password")
    password2 = forms.CharField(widget=forms.PasswordInput, label="Confirm password")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.widget.attrs["class"] = INPUT
        self.fields["role"].widget = forms.HiddenInput()

    @property
    def name_fields(self):
        return [self[n] for n in ("last_name", "first_name", "middle_name")]

    @property
    def other_fields(self):
        order = ("year_level", "course", "department", "email", "id_number", "username", "password1", "password2")
        return [self[n] for n in order]

    def clean_username(self):
        username = self.cleaned_data["username"].strip()
        if User.objects.filter(username__iexact=username).exists():
            raise forms.ValidationError("That username is already taken.")
        return username

    def clean_email(self):
        email = self.cleaned_data["email"].strip()
        if User.objects.filter(email__iexact=email).exists():
            raise forms.ValidationError("An account with this email already exists.")
        return email

    def clean(self):
        data = super().clean()
        role = data.get("role")
        if role == Profile.Role.STUDENT:
            for name in ("year_level", "course"):
                if not data.get(name):
                    self.add_error(name, "This field is required for students.")
        elif role == Profile.Role.PROFESSOR:
            if not data.get("department"):
                self.add_error("department", "This field is required for professors.")

        id_number = (data.get("id_number") or "").strip()
        if role and id_number and Profile.objects.filter(role=role, id_number__iexact=id_number).exists():
            self.add_error("id_number", f"This {role} ID is already registered.")

        p1, p2 = data.get("password1"), data.get("password2")
        if p1 and p2 and p1 != p2:
            self.add_error("password2", "Passwords do not match.")
        elif p1:
            try:
                validate_password(p1)
            except forms.ValidationError as exc:
                self.add_error("password1", exc)
        return data

    def save(self):
        d = self.cleaned_data
        role = d["role"]
        user = User.objects.create_user(
            username=d["username"],
            email=d["email"],
            password=d["password1"],
            first_name=d["first_name"],
            last_name=d["last_name"],
            is_active=False,  # an Admin must approve the account first
        )
        Profile.objects.create(
            user=user,
            role=role,
            status=Profile.Status.PENDING,
            last_name=d["last_name"],
            first_name=d["first_name"],
            middle_name=d["middle_name"],
            year_level=d["year_level"] if role == Profile.Role.STUDENT else "",
            course=d["course"] if role == Profile.Role.STUDENT else "",
            department=d["department"] if role == Profile.Role.PROFESSOR else "",
            id_number=d["id_number"].strip(),
        )
        return user
