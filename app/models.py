from datetime import datetime, timezone
from enum import Enum
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator, model_validator


class Model(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)


class Risk(str, Enum):
    NORMAL = 'NORMAL'
    CAUTION = 'CAUTION'
    DANGER = 'DANGER'


class Login(Model):
    account_id: Literal['guardian-demo', 'admin-demo', 'senior-demo'] = 'guardian-demo'


class LoginResult(Model):
    access_token: str
    token_type: Literal['bearer'] = 'bearer'
    role: Literal['ADMIN', 'GUARDIAN', 'SENIOR']
    user_id: UUID
    mock: bool = True


class PasswordLogin(Model):
    account_id: str = Field(min_length=1, max_length=128, pattern=r'^\S+$')
    password: SecretStr = Field(min_length=1, max_length=256)


class PasswordLoginResult(LoginResult):
    mock: Literal[False] = False
    expires_in: int
    expires_at: str


class Profile(Model):
    user_id: UUID
    account_id: str
    user_name: str
    role: Literal['ADMIN', 'GUARDIAN', 'SENIOR']


class ErrorDetail(Model):
    field: str
    message: str


class Error(Model):
    error_code: str
    message: str
    details: list[ErrorDetail] = Field(default_factory=list)
    request_id: str
    timestamp: str


class Senior(Model):
    senior_id: UUID
    name: str
    timezone: str
    phone_number: str | None


class SeniorList(Model):
    items: list[Senior]


class Device(Model):
    device_id: UUID
    senior_id: UUID
    parent_device_id: UUID | None
    device_type: Literal['GATEWAY','PIEZO_MAT','TOF_SENSOR','CAMERA','SPEAKER']
    connection_status: Literal['ONLINE', 'OFFLINE']
    last_heartbeat_at: str | None
    created_at: str


class DeviceList(Model):
    items: list[Device]


class DeviceSummary(Model):
    online: int
    offline: int


class Status(Model):
    senior_id: UUID
    risk_level: Risk
    last_updated_at: str
    status_event_id: UUID | None
    data_freshness: Literal['FRESH', 'STALE']
    device_summary: DeviceSummary


class AdminSenior(Model):
    senior_id: UUID
    name: str
    risk_level: Risk
    last_updated_at: str
    status_event_id: UUID | None
    data_freshness: Literal['FRESH', 'STALE']
    device_summary: DeviceSummary


class AdminSeniorPage(Model):
    items: list[AdminSenior]
    page: int
    size: int
    total: int
    total_pages: int


class Incident(Model):
    incident_id: UUID
    event_id: UUID
    senior_id: UUID
    event_time: str
    risk_level: Literal['CAUTION','DANGER']
    summary: str
    status: Literal['OPEN','CANCELLED']
    created_at: str


class IncidentPage(Model):
    items: list[Incident]
    page: int
    size: int
    total: int
    total_pages: int


class AdminIncident(Incident):
    senior_name: str


class AdminIncidentPage(Model):
    items: list[AdminIncident]
    page: int
    size: int
    total: int
    total_pages: int


class Evidence(Model):
    piezo_impact_val: float | None
    tof_estimated_height_mm: int | None
    ai_fall_confidence: float | None
    gesture_cancelled: bool
    snapshot_image_url: str | None


AnalysisStatus = Literal['PENDING_VERIFICATION','COMPLETED','CAMERA_UNAVAILABLE','CAMERA_TIMEOUT','INSUFFICIENT_DATA']


class AssessmentState(Model):
    stage: Literal['PRELIMINARY','FINAL']
    analysis_status: AnalysisStatus
    confirmed_fall: bool | None
    camera_confidence: float | None
    last_event_id: UUID
    updated_at: str


class AssessmentRecord(Model):
    event_id: UUID
    device_id: UUID
    occurred_at: str
    received_at: str
    stage: Literal['PRELIMINARY','FINAL']
    analysis_status: AnalysisStatus
    confirmed_fall: bool | None
    camera_confidence: float | None
    summary: str
    applied: bool


class IncidentDetail(Incident):
    evidence: Evidence
    assessment: AssessmentState | None = None
    assessment_history: list[AssessmentRecord] = Field(default_factory=list)
    assessment_history_has_more: bool = False


Day = Literal['MON','TUE','WED','THU','FRI','SAT','SUN']
MedTime = Annotated[str, Field(pattern=r'^([01]\d|2[0-3]):[0-5]\d$', examples=['08:30'])]


class MedicationCreate(Model):
    medication_name: str = Field(min_length=1, max_length=80, examples=['아침약'])
    med_time: MedTime
    days_of_week: list[Day] = Field(min_length=1, max_length=7)
    voice_id: Literal['TYPE_A','TYPE_B'] = 'TYPE_A'
    activity_window_minutes: int = Field(default=30, ge=1, le=180)
    is_active: bool = True

    @field_validator('days_of_week')
    @classmethod
    def unique_days(cls, value):
        if len(set(value)) != len(value):
            raise ValueError('요일은 중복할 수 없습니다.')
        return sorted(value, key=['MON','TUE','WED','THU','FRI','SAT','SUN'].index)


def patch_json_schema(schema):
    # Omission means no change; explicit null is rejected by validate_patch.
    for prop in schema.get('properties',{}).values():
        choices=prop.get('anyOf')
        if choices:
            choices=[x for x in choices if x.get('type')!='null']
            if len(choices)==1:
                prop.pop('anyOf')
                prop.update(choices[0])
            else:
                prop['anyOf']=choices
        if prop.get('default','missing') is None:
            prop.pop('default')


class MedicationPatch(Model):
    model_config=ConfigDict(json_schema_extra=patch_json_schema)
    medication_name: str | None = Field(default=None, min_length=1, max_length=80)
    med_time: MedTime | None = None
    days_of_week: list[Day] | None = Field(default=None, min_length=1, max_length=7)
    voice_id: Literal['TYPE_A','TYPE_B'] | None = None
    activity_window_minutes: int | None = Field(default=None, ge=1, le=180)
    is_active: bool | None = None

    @model_validator(mode='after')
    def validate_patch(self):
        if not self.model_fields_set or any(getattr(self, k) is None for k in self.model_fields_set):
            raise ValueError('변경할 필드를 하나 이상 입력하세요. null은 허용하지 않습니다.')
        if self.days_of_week is not None:
            self.days_of_week = MedicationCreate.unique_days(self.days_of_week)
        return self


class Medication(MedicationCreate):
    schedule_id: UUID
    senior_id: UUID
    timezone: str
    created_at: str
    updated_at: str


class MedicationLog(Model):
    log_id: UUID
    schedule_id: UUID
    source_event_id: UUID | None
    scheduled_date: str
    activity_detected_at: str | None
    voice_played_at: str | None
    play_status: Literal['PENDING','SUCCESS','NO_ACTIVITY','FAILED']


class MedicationLogList(Model):
    items: list[MedicationLog]


class Voice(Model):
    voice_id: str
    label: str
    text: str
    is_active: bool


class VoiceList(Model):
    items: list[Voice]


class PushTokenCreate(Model):
    installation_id: UUID
    provider: Literal['EXPO','FCM']
    push_token: str = Field(min_length=10, max_length=4096)
    device_os: Literal['ANDROID','IOS']


class PushTokenResult(Model):
    token_id: UUID
    installation_id: UUID
    provider: str
    device_os: str
    updated_at: str


class Notification(Model):
    notification_id: UUID
    senior_id: UUID
    user_id: UUID
    source_event_id: UUID | None
    schedule_id: UUID | None
    scheduled_date: str | None
    notification_type: Literal['FALL_ALERT','PATTERN_ALERT','MEDICATION_REMINDER']
    title: str
    body: str
    deep_link: str
    delivery_status: Literal['MOCK_QUEUED','NO_TOKEN']
    created_at: str


class NotificationList(Model):
    items: list[Notification]
    mock: bool = True


class EventInput(Model):
    event_id: UUID
    senior_id: UUID
    device_id: UUID
    occurred_at: datetime

    @field_validator('occurred_at')
    @classmethod
    def aware(cls, value):
        if value.tzinfo is None:
            raise ValueError('시간대가 포함된 ISO 8601 시각을 입력하세요.')
        return value.astimezone(timezone.utc)


class FallAssessment(EventInput):
    incident_id: UUID
    stage: Literal['PRELIMINARY','FINAL']
    analysis_status: AnalysisStatus
    confirmed_fall: bool | None = Field(default=None, strict=True)
    camera_confidence: float | None = Field(default=None, ge=0, le=1)
    summary: str = Field(default='낙상 단계별 판단', min_length=1, max_length=300)

    @model_validator(mode='after')
    def consistent_stage(self):
        if self.stage=='PRELIMINARY':
            if self.analysis_status!='PENDING_VERIFICATION' or self.confirmed_fall is not None or self.camera_confidence is not None:
                raise ValueError('1차 의심은 PENDING_VERIFICATION이며 확정값·카메라 신뢰도를 보내지 않습니다.')
        elif self.analysis_status=='COMPLETED':
            if self.confirmed_fall is None:
                raise ValueError('완료 결과에는 confirmed_fall=true 또는 false가 필요합니다.')
        elif self.analysis_status=='PENDING_VERIFICATION' or self.confirmed_fall is not None or self.camera_confidence is not None:
            raise ValueError('검증 불가는 실패 상태로 구분하고 확정값·신뢰도를 보내지 않습니다.')
        return self


class FallAssessmentResult(Model):
    event_id: UUID
    incident_id: UUID
    duplicate: bool
    state_updated: bool
    incident_status: Literal['OPEN','CANCELLED']
    assessment: AssessmentState


class RiskEvent(EventInput):
    risk_level: Risk
    piezo_impact_val: float | None = Field(default=None, ge=0)
    tof_estimated_height_mm: int | None = Field(default=None, ge=0)
    ai_fall_confidence: float | None = Field(default=None, ge=0, le=1)
    gesture_cancelled: bool = False
    summary: str = Field(default='센서·AI 위험 판정', max_length=300)


class ActivityEvent(EventInput):
    activity_type: Literal['PRESENT','WAKE_UP','MOVEMENT']


class Heartbeat(EventInput):
    connection_status: Literal['ONLINE','OFFLINE'] = 'ONLINE'


class PatternInput(EventInput):
    pattern_type: Literal['NIGHT_ACTIVITY_SURGE','PROLONGED_LYING','INACTIVE']
    observation_start_time: datetime
    observation_end_time: datetime
    metric_value: float = Field(ge=0)
    metric_unit: Literal['count','minutes']
    summary_note: str = Field(default='', max_length=300)
    is_anomaly: bool = False

    @model_validator(mode='after')
    def period(self):
        a, b = self.observation_start_time, self.observation_end_time
        if a.tzinfo is None or b.tzinfo is None or a >= b:
            raise ValueError('관찰 기간은 시간대를 포함하며 시작 < 종료여야 합니다.')
        expected = 'count' if self.pattern_type == 'NIGHT_ACTIVITY_SURGE' else 'minutes'
        if self.metric_unit != expected:
            raise ValueError(f'이 패턴의 단위는 {expected}입니다.')
        return self


class Pattern(Model):
    pattern_id: UUID
    event_id: UUID
    senior_id: UUID
    pattern_type: str
    observation_start_time: str
    observation_end_time: str
    metric_value: float
    metric_unit: str
    summary_note: str
    is_anomaly: bool
    created_at: str


class PatternList(Model):
    items: list[Pattern]


class IngestResult(Model):
    event_id: UUID
    duplicate: bool
    incident_id: UUID | None = None
    pattern_id: UUID | None = None
    command_ids: list[UUID] = Field(default_factory=list)


class Command(Model):
    command_id: UUID
    device_id: UUID
    log_id: UUID
    voice_id: str
    status: Literal['PENDING','SUCCESS','FAILED','EXPIRED']
    created_at: str
    expires_at: str
    acknowledged_at: str | None


class CommandList(Model):
    items: list[Command]


class CommandAck(Model):
    status: Literal['SUCCESS','FAILED']


class FeedEvent(Model):
    cursor: int
    event_id: UUID
    senior_id: UUID
    event_type: Literal['STATUS_CHANGED','INCIDENT_ALERT','DEVICE_STATUS_CHANGED','PATTERN_UPDATED','MEDICATION_UPDATED','MEDICATION_DELETED','SPEAKER_COMMAND_CREATED','SPEAKER_COMMAND_ACK','MEDICATION_REMINDER']
    occurred_at: str
    data: dict


class SyncResult(Model):
    events: list[FeedEvent]
    next_cursor: int
    latest_cursor: int
    has_more: bool


class AdminFeedEvent(FeedEvent):
    senior_name: str
    event_type: Literal['STATUS_CHANGED','INCIDENT_ALERT','DEVICE_STATUS_CHANGED','PATTERN_UPDATED']


class AdminSyncResult(Model):
    events: list[AdminFeedEvent]
    next_cursor: int
    latest_cursor: int
    has_more: bool


class AdminSubscription(Model):
    action: Literal['SUBSCRIBE']
    access_token: str = Field(default='', max_length=256)
    after_cursor: int = Field(default=0, strict=True, ge=0, le=9_007_199_254_740_991)


class Scenario(Model):
    senior_id: UUID
    scenario: Literal['NORMAL','CAUTION','DANGER','CANCELLED','OFFLINE','ONLINE','PATTERN','ACTIVITY']


class ScenarioResult(Model):
    scenario: str
    event_id: UUID
    result: IngestResult


class ResetResult(Model):
    reset: bool
    message: str


class ReminderTickResult(Model):
    generated_count: int
    processed_at: str
    mock: bool = True
