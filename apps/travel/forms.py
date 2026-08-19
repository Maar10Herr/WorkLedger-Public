"""Settings forms for travel records.

Validation happens here, before any database write, so malformed input
re-renders the section page with inline errors instead of surfacing a raw
database error (the confirmed ``country_code`` 500 was exactly that: an
unvalidated ``varchar(2)`` overflow on PostgreSQL).

The legacy free-form country input is replaced by a checked-in ISO country
selector (``apps/travel/countries.py``) that also normalises common aliases
(``de``, ``Germany``, ``Deutschland`` → ``DE``).
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any, ClassVar, cast

from django import forms
from django.core.exceptions import ValidationError
from django.forms.models import InlineForeignKeyField, construct_instance

from apps.taxes.models import RouteDistance

from .countries import COUNTRY_CHOICES, normalize_country_code
from .models import Employer, Location, LocationType, RailPass

FIELD_INPUT = "wl-field__input"


class LocationForm(forms.ModelForm):  # type: ignore[type-arg]
    """Validated creation of a saved location.

    ``country_code`` is declared explicitly (not derived from the model) so
    full names such as ``Germany`` can be normalised *before* the ``varchar(2)``
    model limit would otherwise reject them, and so the widget is a human
    country selector rather than a free-form code field.
    """

    name = forms.CharField(
        max_length=200,
        strip=True,
        widget=forms.TextInput(
            attrs={
                "class": FIELD_INPUT,
                "id": "location-name",
                "required": True,
                "placeholder": "e.g. Hamburg office",
            }
        ),
    )
    location_type = forms.ChoiceField(
        choices=LocationType.choices,
        widget=forms.Select(attrs={"class": FIELD_INPUT, "id": "location-type"}),
    )
    address = forms.CharField(
        required=False,
        strip=True,
        widget=forms.TextInput(
            attrs={
                "class": FIELD_INPUT,
                "id": "location-address",
                "placeholder": "Street and number",
            }
        ),
    )
    country_code = forms.CharField(
        required=False,
        max_length=64,
        label="country",
        widget=forms.Select(
            choices=COUNTRY_CHOICES,
            attrs={"class": FIELD_INPUT, "id": "location-country"},
        ),
    )
    locality = forms.CharField(
        required=False,
        max_length=120,
        strip=True,
        widget=forms.TextInput(
            attrs={
                "class": FIELD_INPUT,
                "id": "location-city",
                "placeholder": "City",
            }
        ),
    )
    latitude = forms.DecimalField(
        required=False,
        max_digits=9,
        decimal_places=6,
        min_value=Decimal("-90"),
        max_value=Decimal("90"),
        widget=forms.NumberInput(
            attrs={
                "class": FIELD_INPUT,
                "id": "location-lat",
                "step": "0.000001",
                "placeholder": "49.006900",
            }
        ),
    )
    longitude = forms.DecimalField(
        required=False,
        max_digits=9,
        decimal_places=6,
        min_value=Decimal("-180"),
        max_value=Decimal("180"),
        widget=forms.NumberInput(
            attrs={
                "class": FIELD_INPUT,
                "id": "location-lng",
                "step": "0.000001",
                "placeholder": "8.403700",
            }
        ),
    )
    station_name = forms.CharField(
        required=False,
        max_length=200,
        strip=True,
        widget=forms.TextInput(
            attrs={
                "class": FIELD_INPUT,
                "id": "location-station",
                "placeholder": "Station",
            }
        ),
    )
    is_favourite = forms.BooleanField(
        required=False, widget=forms.CheckboxInput(attrs={"class": "wl-check__input"})
    )
    is_default_residence = forms.BooleanField(
        required=False,
        label="default residence",
        widget=forms.CheckboxInput(attrs={"class": "wl-check__input"}),
    )

    class Meta:
        model = Location
        fields: ClassVar[tuple[str, ...]] = (
            "name",
            "location_type",
            "address",
            "country_code",
            "locality",
            "latitude",
            "longitude",
            "station_name",
            "is_favourite",
            "is_default_residence",
        )

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        # Human-readable country selection, defaulting to Germany (DE).
        self.fields["country_code"].initial = "DE"
        # The single-default-residence constraint is enforced transactionally
        # in the view (demote the previous default, then insert, under row
        # locks; the partial unique index remains the backstop and its
        # IntegrityError is caught and turned into a retryable form error).
        # Validating it here would reject the legitimate replace-the-default
        # flow, because the previous default still exists when the form runs.
    def validate_unique(self) -> None:
        """Defer the partial unique default check to the atomic save.

        ModelForm's normal uniqueness validation runs before the view can
        demote the existing default residence, so it incorrectly rejects a
        legitimate default replacement. The database constraint remains
        active and the transaction catches any race safely.
        """
        return None

    def _post_clean(self) -> None:
        """Run model validation without pre-transaction constraint checks.

        Django 5.2's ``ModelForm._post_clean`` calls ``full_clean`` with
        constraint validation enabled. That sees the old default residence
        before ``_save_location_form`` can demote it, so a valid replacement
        is rejected. The view's atomic save is the authoritative constraint
        boundary for this replacement flow.
        """
        opts = self._meta
        exclude = self._get_validation_exclusions()
        for name, field in self.fields.items():
            if isinstance(field, InlineForeignKeyField):
                exclude.add(name)
        try:
            self.instance = construct_instance(self, self.instance, opts.fields, opts.exclude)
        except ValidationError as error:
            self._update_errors(error)  # type: ignore[attr-defined]
        try:
            self.instance.full_clean(
                exclude=exclude,
                validate_unique=False,
                validate_constraints=False,
            )
        except ValidationError as error:
            self._update_errors(error)  # type: ignore[attr-defined]
        if getattr(self, "_validate_unique", False):
            self.validate_unique()

    def _get_validation_exclusions(self) -> set[str]:
        exclusions = cast(
            set[str], super()._get_validation_exclusions()  # type: ignore[misc]
        )
        # The partial unique constraint is checked by the atomic view save;
        # excluding the flag here allows a legitimate replacement to reach
        # that transaction instead of failing while the old default remains.
        exclusions.add("is_default_residence")
        return exclusions

    def clean_country_code(self) -> str:
        raw = self.cleaned_data.get("country_code") or ""
        value = raw.strip()
        if not value:
            return ""
        normalized = normalize_country_code(value)
        if normalized is None:
            raise forms.ValidationError("Select a valid country.")
        return normalized

    def clean(self) -> dict[str, Any]:
        cleaned_data = super().clean() or {}
        location_type = cleaned_data.get("location_type")
        if cleaned_data.get("is_default_residence") and location_type != LocationType.RESIDENCE:
            self.add_error(
                "is_default_residence",
                "Only a residence can be the default residence.",
            )
        return cleaned_data


class EmployerForm(forms.ModelForm):  # type: ignore[type-arg]
    """Set (or replace) the active employer; the replacement itself is
    transactional in the view so a failed validation leaves the previous
    active employer untouched."""

    name = forms.CharField(
        max_length=200,
        strip=True,
        widget=forms.TextInput(
            attrs={
                "class": FIELD_INPUT,
                "id": "employer-name",
                "required": True,
                "placeholder": "Employer name",
            }
        ),
    )

    class Meta:
        model = Employer
        fields: ClassVar[tuple[str, ...]] = ("name",)

    def save(self, commit: bool = True) -> Employer:
        self.instance.is_active = True
        return cast(Employer, super().save(commit=commit))


class RailPassForm(forms.ModelForm):  # type: ignore[type-arg]
    """Rail pass with validated dates, order, and optional cost."""

    name = forms.CharField(
        max_length=200,
        strip=True,
        widget=forms.TextInput(
            attrs={
                "class": FIELD_INPUT,
                "id": "pass-name",
                "required": True,
                "placeholder": "e.g. BahnCard 100",
            }
        ),
    )
    pass_type = forms.CharField(
        max_length=50,
        strip=True,
        widget=forms.TextInput(
            attrs={
                "class": FIELD_INPUT,
                "id": "pass-type",
                "required": True,
                "placeholder": "e.g. unlimited / local",
            }
        ),
    )
    valid_from = forms.DateField(
        widget=forms.DateInput(attrs={"class": FIELD_INPUT, "id": "pass-from", "type": "date"})
    )
    valid_to = forms.DateField(
        widget=forms.DateInput(attrs={"class": FIELD_INPUT, "id": "pass-to", "type": "date"})
    )
    purchase_cost = forms.DecimalField(
        required=False,
        max_digits=12,
        decimal_places=2,
        min_value=Decimal("0"),
        error_messages={"min_value": "The cost must be a nonnegative number."},
        widget=forms.NumberInput(
            attrs={"class": FIELD_INPUT, "id": "pass-cost", "min": "0", "step": "0.01"}
        ),
    )

    class Meta:
        model = RailPass
        fields: ClassVar[tuple[str, ...]] = (
            "name",
            "pass_type",
            "valid_from",
            "valid_to",
            "purchase_cost",
        )

    def clean(self) -> dict[str, Any]:
        cleaned_data = super().clean() or {}
        valid_from = cleaned_data.get("valid_from")
        valid_to = cleaned_data.get("valid_to")
        if valid_from and valid_to and valid_to < valid_from:
            self.add_error("valid_to", "The end date must be after the start date.")
        return cleaned_data


class _RouteLocationsForm(forms.Form):
    """Shared origin/destination validation for route forms."""

    origin = forms.UUIDField()
    destination = forms.UUIDField()

    error_messages: ClassVar[dict[str, str]] = {
        "invalid": "Select a saved location.",
        "required": "Choose an origin and a destination.",
    }

    def clean(self) -> dict[str, Any]:
        cleaned_data = super().clean() or {}
        origin_uuid = cleaned_data.get("origin")
        destination_uuid = cleaned_data.get("destination")
        if origin_uuid and destination_uuid:
            if origin_uuid == destination_uuid:
                self.add_error(
                    "destination",
                    "The origin and destination must not be the same location.",
                )
            origin = Location.objects.filter(pk=origin_uuid).first()
            destination = Location.objects.filter(pk=destination_uuid).first()
            cleaned_data["origin"] = origin
            cleaned_data["destination"] = destination
            if origin is None:
                self.add_error("origin", "Select a saved location for the origin.")
            if destination is None:
                self.add_error("destination", "Select a saved location for the destination.")
        return cleaned_data


class RouteLookupForm(_RouteLocationsForm):
    """Provider lookup of the shortest road route between two saved locations."""


class RouteConfirmationForm(_RouteLocationsForm):
    """Manual confirmation of a route with a measured distance."""

    route_requires_reason = False

    distance_km = forms.DecimalField(
        max_digits=10,
        decimal_places=2,
        min_value=Decimal("0"),
        error_messages={
            "invalid": "Enter a valid distance in kilometres.",
            "required": "Enter the one-way distance in kilometres.",
            "max_digits": "That distance is too large.",
            "max_decimal_places": "Use at most two decimal places.",
            "min_value": "The distance cannot be negative.",
        },
    )
    route_comment = forms.CharField(
        required=False,
        max_length=RouteDistance._meta.get_field("override_comment").max_length,
        strip=True,
        error_messages={"max_length": "Use at most 500 characters."},
    )

    def clean(self) -> dict[str, Any]:
        cleaned_data = super().clean() or {}
        origin = cleaned_data.get("origin")
        destination = cleaned_data.get("destination")
        if origin is not None and destination is not None:
            self.route_requires_reason = RouteDistance.objects.filter(
                origin=origin,
                destination=destination,
                mode="driving",
                confirmed=True,
            ).exclude(source="manual").exists()
            if self.route_requires_reason and not cleaned_data.get("route_comment"):
                self.add_error(
                    "route_comment",
                    "An override reason is required when replacing a calculated route.",
                )
        return cleaned_data


# Kept as an import-compatible name for callers outside the settings view.
RouteManualForm = RouteConfirmationForm


class RouteConfirmForm(forms.Form):
    """Confirm a provider candidate, optionally correcting its distance."""

    candidate = forms.UUIDField(
        error_messages={
            "invalid": "Select a valid candidate route.",
            "required": "Select a candidate route.",
        }
    )
    distance_km = forms.DecimalField(
        required=False,
        max_digits=10,
        decimal_places=2,
        min_value=Decimal("0"),
        error_messages={
            "invalid": "Enter a valid distance in kilometres.",
            "max_digits": "That distance is too large.",
            "max_decimal_places": "Use at most two decimal places.",
            "min_value": "The distance cannot be negative.",
        },
    )
    route_comment = forms.CharField(
        required=False,
        max_length=RouteDistance._meta.get_field("override_comment").max_length,
        strip=True,
        error_messages={"max_length": "Use at most 500 characters."},
    )

    def clean(self) -> dict[str, Any]:
        cleaned_data = super().clean() or {}
        candidate_uuid = cleaned_data.get("candidate")
        if candidate_uuid:
            candidate = RouteDistance.objects.filter(pk=candidate_uuid).first()
            cleaned_data["candidate"] = candidate
            if candidate is None:
                self.add_error("candidate", "The candidate route no longer exists.")
        if cleaned_data.get("distance_km") is not None and not (
            cleaned_data.get("route_comment") or ""
        ).strip():
            self.add_error("route_comment", "A correction requires a confirmation reason.")
        return cleaned_data
