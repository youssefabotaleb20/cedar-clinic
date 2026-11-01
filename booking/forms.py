from django import forms
from django.contrib.auth.forms import UserCreationForm
from django.contrib.auth.models import User


class SignUpForm(UserCreationForm):
    first_name = forms.CharField(max_length=150)
    last_name = forms.CharField(max_length=150)
    email = forms.EmailField(help_text="We send your confirmations and reminders here.")

    class Meta(UserCreationForm.Meta):
        model = User
        fields = ("username", "first_name", "last_name", "email")

    def clean_email(self):
        email = self.cleaned_data["email"].lower()
        if User.objects.filter(email__iexact=email).exists():
            raise forms.ValidationError("An account with this email already exists.")
        return email


class BookingForm(forms.Form):
    date = forms.DateField(widget=forms.HiddenInput)
    start_time = forms.TimeField(widget=forms.HiddenInput)
    reason = forms.CharField(
        max_length=255,
        required=False,
        widget=forms.TextInput(attrs={"placeholder": "Optional: what is the visit about?"}),
    )
