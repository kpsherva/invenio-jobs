# -*- coding: utf-8 -*-
#
# Copyright (C) 2024 CERN.
#
# Invenio-Jobs is free software; you can redistribute it and/or modify it
# under the terms of the MIT License; see LICENSE file for more details.

"""Service schemas."""

import inspect
from copy import deepcopy
from datetime import timezone

from invenio_i18n import lazy_gettext as _
from invenio_users_resources.services import schemas as user_schemas
from marshmallow import EXCLUDE, Schema, fields, validate, types, post_load, pre_dump
from marshmallow_oneofschema import OneOfSchema
from marshmallow_utils.fields import SanitizedUnicode, TZDateTime
from marshmallow_utils.permissions import FieldPermissionsMixin
from marshmallow_utils.validators import LazyOneOf

from ..models import RunStatusEnum, Task
from ..proxies import current_jobs


def _not_blank(**kwargs):
    """Returns a non-blank validation rule."""
    max_ = kwargs.get("max", "")
    return validate.Length(
        error=_(
            "Field cannot be blank or longer than {max_} characters.".format(max_=max_)
        ),
        min=1,
        **kwargs,
    )


class TaskParameterSchema(Schema):
    """Schema for a task parameter."""

    name = SanitizedUnicode()

    # TODO: Make custom schema for serializing parameter types
    default = fields.Method("dump_default")
    kind = fields.String()

    def dump_default(self, obj):
        """Dump the default value."""
        if obj.default in (None, inspect.Parameter.empty):
            return None
        elif isinstance(obj.default, (bool, int, float, str)):
            return obj.default
        else:
            return str(obj.default)


class TaskSchema(Schema, FieldPermissionsMixin):
    """Schema for a task."""

    name = SanitizedUnicode()
    description = SanitizedUnicode()
    parameters = fields.Dict(
        keys=SanitizedUnicode(),
        values=fields.Nested(TaskParameterSchema),
    )


class IntervalScheduleSchema(Schema):
    """Schema for an interval schedule based on ``datetime.timedelta``."""

    type = fields.Constant("interval")

    days = fields.Integer()
    seconds = fields.Integer()
    microseconds = fields.Integer()
    milliseconds = fields.Integer()
    minutes = fields.Integer()
    hours = fields.Integer()
    weeks = fields.Integer()


class CrontabScheduleSchema(Schema):
    """Schema for a crontab schedule."""

    type = fields.Constant("crontab")

    minute = fields.String(load_default="*")
    hour = fields.String(load_default="*")
    day_of_week = fields.String(load_default="*")
    day_of_month = fields.String(load_default="*")
    month_of_year = fields.String(load_default="*")


class CustomArgsSchema(Schema):
    args = fields.Raw(load_default=dict, allow_none=True)


class ScheduleSchema(OneOfSchema):
    """Schema for a schedule."""

    def get_obj_type(self, obj):
        """Get type from object data."""
        if isinstance(obj, dict) and "type" in obj:
            return obj["type"]

    type_schemas = {
        "interval": IntervalScheduleSchema,
        "crontab": CrontabScheduleSchema,
    }
    type_field_remove = False


class RegisteredTaskArgumentsSchema(OneOfSchema):

    type_field_remove = False

    def __init__(self, *args, **kwargs):
        self.type_schemas = deepcopy(current_jobs.registry.registered_schemas())
        self.type_schemas["custom"] = CustomArgsSchema
        super().__init__(*args, **kwargs)

    def get_obj_type(self, obj):
        if isinstance(obj, dict) and "type" in obj:
            return obj["type"]
        if isinstance(obj, dict) and "type" not in obj:
            return "custom"

    def get_data_type(self, data):
        data_type = super().get_data_type(data)
        if data_type is None:
            return "custom"


class JobSchema(Schema, FieldPermissionsMixin):
    """Base schema for a job."""

    class Meta:
        """Meta attributes for the schema."""

        unknown = EXCLUDE

    id = fields.UUID(dump_only=True)

    created = TZDateTime(timezone=timezone.utc, format="iso", dump_only=True)
    updated = TZDateTime(timezone=timezone.utc, format="iso", dump_only=True)

    title = SanitizedUnicode(required=True, validate=_not_blank(max=250))
    description = SanitizedUnicode()

    active = fields.Boolean(load_default=True)

    task = fields.String(
        required=True,
        validate=LazyOneOf(choices=lambda: [name for name, t in Task.all().items()])
    )
    default_queue = fields.String(
        validate=LazyOneOf(choices=lambda: current_jobs.queues.keys()),
        load_default=lambda: current_jobs.default_queue,
    )

    default_args = fields.Raw(dump_only=True)

    schedule = fields.Nested(ScheduleSchema, allow_none=True, load_default=None)

    last_run = fields.Nested(lambda: RunSchema, dump_only=True)
    last_runs = fields.Raw(dump_only=True)

    @pre_dump
    def dump_last_runs(self, obj, many=False, **kwargs):
        last_runs = obj.get("last_runs", {})
        for key, value in last_runs.items():
            if value:
                last_runs[key] = RunSchema().dump(value.dump())
        return obj


class UserSchema(OneOfSchema):
    """User schema."""

    def get_obj_type(self, obj):
        """Get type from object data."""
        return "system" if obj is None else "user"

    type_schemas = {
        "user": user_schemas.UserSchema,
        "system": user_schemas.SystemUserSchema,
    }


class RunSchema(Schema, FieldPermissionsMixin):
    """Base schema for a job run."""

    class Meta:
        """Meta attributes for the schema."""

        unknown = EXCLUDE

    id = fields.UUID(dump_only=True)
    job_id = fields.UUID(dump_only=True)

    created = TZDateTime(timezone=timezone.utc, format="iso", dump_only=True)
    updated = TZDateTime(timezone=timezone.utc, format="iso", dump_only=True)

    started_by_id = fields.Integer(dump_only=True)
    started_by = fields.Nested(UserSchema, dump_only=True)

    started_at = TZDateTime(timezone=timezone.utc, format="iso", dump_only=True)
    finished_at = TZDateTime(timezone=timezone.utc, format="iso", dump_only=True)

    status = fields.Enum(RunStatusEnum, dump_only=True)
    message = SanitizedUnicode(dump_only=True)

    task_id = fields.UUID(dump_only=True)

    # Input fields
    title = SanitizedUnicode(validate=_not_blank(max=250), dump_default="Manual run")
    args = fields.Nested(
        lambda: RegisteredTaskArgumentsSchema,
        metadata={
            "type": "dynamic",
            "endpoint": "/api/tasks/<item_id>/args",
            "depends_on": "task",
        },
    )
    custom_args = fields.Raw(
        load_default=dict,
        allow_none=True,
        metadata={
            "title": "Custom args",
            "description": "Advanced configuration for seasoned administrators",
        },
    )
    queue = fields.String(
        validate=LazyOneOf(choices=lambda: current_jobs.queues.keys()),
        load_default=lambda: current_jobs.default_queue,
        dump_default=lambda: current_jobs.default_queue,
    )

    @post_load
    def pick_args(self, obj, many, **kwargs):
        custom_args = obj.pop("custom_args")
        if custom_args:
            obj["args"] = custom_args
        return obj
