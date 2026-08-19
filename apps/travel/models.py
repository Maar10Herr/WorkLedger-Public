from __future__ import annotations

import uuid
from typing import ClassVar

from django.db import models


class LocationType(models.TextChoices):
    RESIDENCE = "residence", "Residence"
    FIRST_WORKPLACE = "first_workplace", "First workplace"
    EMPLOYER_OFFICE = "employer_office", "Another employer office"
    CLIENT_SITE = "client_site", "Client site"
    OTHER_EXTERNAL = "other_external", "Other external location"


class TransportMode(models.TextChoices):
    TRAIN = "train", "Train"
    PRIVATE_CAR = "private_car", "Private car"
    EMPLOYER_CAR = "employer_car", "Employer car"
    PASSENGER = "passenger", "Passenger in another person's car"
    TAXI = "taxi", "Taxi or ride service"
    LOCAL_TRANSIT = "local_public_transport", "Local public transport"
    BICYCLE = "bicycle", "Bicycle"
    WALKING = "walking", "Walking"
    PLANE = "plane", "Plane"
    FERRY = "ferry", "Ferry"
    OTHER = "other", "Other"


class Employer(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=200)
    is_active = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints: ClassVar[list[models.BaseConstraint]] = [
            models.UniqueConstraint(
                fields=["is_active"],
                condition=models.Q(is_active=True),
                name="single_active_employer",
            )
        ]

    def __str__(self) -> str:
        return self.name


class Location(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=200)
    location_type = models.CharField(max_length=32, choices=LocationType.choices)
    address = models.TextField(blank=True)
    country_code = models.CharField(max_length=2, blank=True)
    locality = models.CharField(max_length=120, blank=True)
    latitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    longitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    station_name = models.CharField(max_length=200, blank=True)
    is_default_residence = models.BooleanField(default=False)
    is_favourite = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering: ClassVar[list[str]] = ["name"]
        constraints: ClassVar[list[models.BaseConstraint]] = [
            models.UniqueConstraint(
                fields=["is_default_residence"],
                condition=models.Q(is_default_residence=True),
                name="single_default_residence",
            )
        ]

    def __str__(self) -> str:
        return self.name


class RailPass(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=200)
    pass_type = models.CharField(max_length=50)
    valid_from = models.DateField()
    valid_to = models.DateField()
    purchase_cost = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    attachment_event_id = models.UUIDField(null=True, blank=True)

    class Meta:
        ordering: ClassVar[list[str]] = ["-valid_from", "name"]
        constraints: ClassVar[list[models.BaseConstraint]] = [
            models.CheckConstraint(
                condition=models.Q(valid_to__gte=models.F("valid_from")),
                name="rail_pass_dates_ordered",
            )
        ]

    def __str__(self) -> str:
        return self.name


class ExternalActivity(models.Model):
    event = models.OneToOneField(
        "ledger.Event",
        on_delete=models.PROTECT,
        primary_key=True,
        related_name="external_activity_identity",
    )
    journey_legs = models.ManyToManyField(
        "ledger.Event", related_name="linked_external_activities", blank=True
    )
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self) -> str:
        return f"External activity {self.event_id}"
