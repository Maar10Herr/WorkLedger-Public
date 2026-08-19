from typing import Any

from django import forms
from django.core.exceptions import ValidationError

from .services import validate_pin


class PinLoginForm(forms.Form):
    pin = forms.CharField(
        label="PIN",
        strip=True,
        widget=forms.PasswordInput(
            attrs={
                "autocomplete": "current-password",
                "inputmode": "numeric",
                "pattern": "[0-9]*",
                "minlength": "4",
                "autofocus": True,
            }
        ),
    )


class InitialPinSetupForm(forms.Form):
    pin = forms.CharField(
        label="New PIN",
        strip=True,
        widget=forms.PasswordInput(
            attrs={
                "autocomplete": "new-password",
                "inputmode": "numeric",
                "pattern": "[0-9]*",
                "minlength": "4",
                "autofocus": True,
            }
        ),
    )
    confirmation = forms.CharField(
        label="Repeat PIN",
        strip=True,
        widget=forms.PasswordInput(
            attrs={
                "autocomplete": "new-password",
                "inputmode": "numeric",
                "pattern": "[0-9]*",
                "minlength": "4",
            }
        ),
    )

    def clean_pin(self) -> str:
        pin = str(self.cleaned_data["pin"])
        try:
            validate_pin(pin)
        except ValidationError as exc:
            raise forms.ValidationError(exc.messages[0]) from exc
        return pin

    def clean(self) -> dict[str, Any]:
        cleaned_data = super().clean() or {}
        pin = cleaned_data.get("pin")
        confirmation = cleaned_data.get("confirmation")
        if pin and confirmation and pin != confirmation:
            self.add_error("confirmation", "PIN entries do not match.")
        return cleaned_data
