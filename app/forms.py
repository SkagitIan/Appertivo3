from django import forms
from django.contrib.auth.forms import UserCreationForm
from django.contrib.auth.models import User

from .models import Special


class SpecialForm(forms.ModelForm):
    """Form for creating and editing specials."""

    class Meta:
        model = Special
        fields = [
            "title",
            "description",
            "price",
            "image",
            "start_date",
            "end_date",
            "cta_type",
            "cta_url",
            "cta_phone",
        ]


class ContactForm(forms.Form):
    """Simple contact form."""

    name = forms.CharField(max_length=100)
    email = forms.EmailField()
    message = forms.CharField(widget=forms.Textarea)


class SubUserCreationForm(UserCreationForm):
    """User creation form requiring an email for subusers."""

    email = forms.EmailField(required=True)

    class Meta(UserCreationForm.Meta):
        model = User
        fields = UserCreationForm.Meta.fields + ("email",)
