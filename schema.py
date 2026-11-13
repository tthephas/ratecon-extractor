"""Data contract for a carrier-facing rate confirmation."""
from datetime import date, time
from decimal import Decimal
from typing import Literal, Optional

from pydantic import BaseModel, Field, model_validator


class TimeWindow(BaseModel):
    kind: Literal["appointment", "appointment_to_schedule", "window", "fcfs"] = Field(
        description=(
            "appointment = a set appointment time; "
            "appointment_to_schedule = appointment required but not yet booked (carrier must call), "
            "start/end hold the hours an appointment can be booked within, if given; "
            "window = start-end range; fcfs = first come first served"
        )
    )
    date: date
    start: Optional[time] = None
    end: Optional[time] = None

    @model_validator(mode="after")
    def times_match_kind(self):
        if self.kind == "appointment" and self.start is None:
            raise ValueError("an appointment needs a start time")
        if self.kind == "window" and (self.start is None or self.end is None):
            raise ValueError("a window needs both start and end times")
        return self


class Location(BaseModel):
    facility_name: str
    address: str
    city: str
    state: str = Field(min_length=2, max_length=2, description="2-letter state code")
    zip_code: Optional[str] = None


class Reference(BaseModel):
    kind: str = Field(description="e.g. PO, pickup number, appointment number, BOL")
    value: str


class Stop(BaseModel):
    sequence: int = Field(ge=1, description="order of the stop on the load, starting at 1")
    stop_type: Literal["pickup", "delivery"]
    location: Location
    time: TimeWindow
    references: list[Reference] = []
    notes: list[str] = Field(default=[], description="stop instructions, e.g. 'no parking on site'")


class Commodity(BaseModel):
    description: str
    packaging: Optional[str] = Field(default=None, description="e.g. palletized, floor loaded")
    pallet_count: Optional[int] = None
    weight_lbs: Optional[int] = None


class Accessorial(BaseModel):
    name: str = Field(description="e.g. detention, stop-off pay, or a deduction like late fee")
    amount: Decimal = Field(description="positive adds to pay; negative for deductions already applied")


class Reimbursement(BaseModel):
    name: str = Field(description="e.g. lumper")
    amount: Optional[Decimal] = Field(default=None, description="cap or amount if stated")
    condition: Optional[str] = Field(default=None, description="e.g. 'with valid receipt'")


class Rate(BaseModel):
    total: Decimal = Field(description="total agreed rate on the rate con, excluding reimbursements")
    linehaul: Optional[Decimal] = None
    fuel_surcharge: Optional[Decimal] = None
    accessorials: list[Accessorial] = Field(
        default=[], description="charges included in the total, e.g. stop-off pay"
    )
    reimbursements: list[Reimbursement] = Field(
        default=[], description="paid later and NOT in the total, e.g. lumper with receipt"
    )

    @model_validator(mode="after")
    def parts_add_up(self):
        if self.linehaul is not None and self.fuel_surcharge is not None:
            parts = self.linehaul + self.fuel_surcharge + sum(a.amount for a in self.accessorials)
            if parts != self.total:
                raise ValueError(
                    f"rate parts add up to {parts} but stated total is {self.total}"
                )
        return self


class Penalty(BaseModel):
    """A warning: a fine charged ONLY if the carrier fails an obligation. Not yet charged."""
    reason: str = Field(description="e.g. late delivery, no automated tracking")
    amount: Optional[Decimal] = None
    source: Literal["load_specific", "terms_and_conditions"] = Field(
        description="load_specific = written for this load; terms_and_conditions = standard boilerplate"
    )


class RateConfirmation(BaseModel):
    broker_name: str
    broker_mc: Optional[str] = None
    broker_load_number: str = Field(description="the broker's load/reference number for invoicing")
    references: list[Reference] = Field(
        default=[], description="load-level references not tied to one stop, e.g. customer PO"
    )
    equipment: str = Field(description="e.g. 53' dry van, 53' reefer, flatbed")
    stops: list[Stop]
    commodity: Optional[Commodity] = None
    rate: Rate
    penalties: list[Penalty] = Field(
        default=[], description="warnings of fines that apply only if an obligation is missed"
    )
    tracking_required: bool = False
    conflicts: list[str] = Field(
        default=[],
        description="contradictions found in the document, e.g. two notes disagreeing on who pays the lumper",
    )
    general_notes: list[str] = Field(
        default=[], description="anything else important, e.g. reefer temperature, detention terms"
    )

    @model_validator(mode="after")
    def stops_make_sense(self):
        if len(self.stops) < 2:
            raise ValueError("a load needs at least one pickup and one delivery")
        ordered = sorted(self.stops, key=lambda s: s.sequence)
        if ordered[0].stop_type != "pickup":
            raise ValueError("first stop must be a pickup")
        if ordered[-1].stop_type != "delivery":
            raise ValueError("last stop must be a delivery")
        return self
