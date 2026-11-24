import type { Schemas } from './schemas';
export type Medication = Schemas['Medication'];
export type MedicationCreate = Schemas['MedicationCreate'];
export type ApiFailure = Schemas['Error'];
export type FeedEvent = Schemas['FeedEvent'];
export type AdminStreamEvent = Schemas['AdminFeedEvent'] & {type:'EVENT'; replayed:boolean};

export class ApiError extends Error {
  constructor(public status: number, public payload: ApiFailure) { super(payload.message); }
}

/** baseUrl: http://localhost:8000 또는 서버 PC의 LAN 주소. */
export function createApi(baseUrl: string, getToken: () => string) {
  const root = baseUrl.replace(/\/$/, '');
  async function request<T>(path: string, options: {method?: string; body?: unknown; idempotencyKey?: string} = {}): Promise<T> {
    const response = await fetch(`${root}/api/v1${path}`, {
      method: options.method || 'GET',
      headers: {Authorization: `Bearer ${getToken()}`,
        ...(options.body !== undefined ? {'Content-Type': 'application/json'} : {}),
        ...(options.idempotencyKey ? {'Idempotency-Key': options.idempotencyKey} : {})},
      ...(options.body !== undefined ? {body: JSON.stringify(options.body)} : {})
    });
    if (response.status === 204) return undefined as T;
    const payload = await response.json();
    if (!response.ok) throw new ApiError(response.status, payload);
    return payload as T;
  }
  return {
    passwordLogin: (account_id: string, password: string) => request<Schemas['PasswordLoginResult']>('/auth/login', {method:'POST',body:{account_id,password}}),
    me: () => request<Schemas['Profile']>('/auth/me'),
    logout: () => request<void>('/auth/logout', {method:'POST'}),
    login: (account_id: 'guardian-demo' | 'admin-demo' | 'senior-demo' = 'guardian-demo') => request<Schemas['LoginResult']>('/auth/mock-login', {method:'POST',body:{account_id}}),
    seniors: () => request<Schemas['SeniorList']>('/seniors'),
    adminSeniors: (page = 1, size = 20) => request<Schemas['AdminSeniorPage']>(`/admin/seniors?page=${page}&size=${size}`),
    adminIncidents: (query: {page?: number; size?: number; senior_id?: string; start_at?: string; end_at?: string} = {}) => {
      const params = new URLSearchParams();
      Object.entries(query).forEach(([k,v]) => {if(v !== undefined) params.set(k,String(v));});
      return request<Schemas['AdminIncidentPage']>(`/admin/incidents?${params}`);
    },
    adminSync: (after_cursor=0,limit=100) => request<Schemas['AdminSyncResult']>(`/admin/sync?after_cursor=${after_cursor}&limit=${limit}`),
    ingestFallAssessment: (body: Schemas['FallAssessment']) => request<Schemas['FallAssessmentResult']>('/ingest/fall-assessments',{method:'POST',body}),
    previewFallAssessment: (body: Schemas['FallAssessment']) => request<Schemas['FallAssessmentResult']>('/mock/fall-assessments',{method:'POST',body}),
    /** after_cursor는 관리자 계정+서버별 저장. 재연결 시 다시 호출하고 이전 소켓은 닫습니다. */
    subscribeAdmin: (afterCursor: number, onEvent: (event: AdminStreamEvent) => void, onCursor: (cursor: number) => void, onError: (code: string,message: string) => void, onClose: (code: number) => void = () => {}) => {
      let cursor=afterCursor;
      const ws=new WebSocket(`${root.replace(/^http/,'ws')}/ws/admin/live`);
      const ping=setInterval(()=>{if(ws.readyState===WebSocket.OPEN)ws.send(JSON.stringify({action:'PING'}));},20000);
      ws.onopen=()=>ws.send(JSON.stringify({action:'SUBSCRIBE',access_token:getToken(),after_cursor:cursor}));
      ws.onmessage=message=>{
        const data=JSON.parse(message.data as string);
        if(data.type==='EVENT' && data.cursor>cursor){onEvent(data as AdminStreamEvent);cursor=data.cursor;onCursor(cursor);}
        if(data.type==='CURSOR' && data.cursor>=cursor){cursor=data.cursor;onCursor(cursor);}
        if(data.type==='ERROR')onError(data.error_code,data.message);
      };
      ws.onclose=event=>{clearInterval(ping);onClose(event.code);};
      return ws;
    },
    status: (id: string) => request<Schemas['Status']>(`/seniors/${id}/status`),
    devices: (id: string) => request<Schemas['DeviceList']>(`/seniors/${id}/devices`),
    incidents: (id: string, query: {page?: number; size?: number; start_at?: string; end_at?: string} = {}) => {
      const params = new URLSearchParams();
      Object.entries(query).forEach(([k,v]) => {if (v !== undefined) params.set(k,String(v));});
      return request<Schemas['IncidentPage']>(`/seniors/${id}/incidents?${params}`);
    },
    incident: (id: string) => request<Schemas['IncidentDetail']>(`/incidents/${id}`),
    patterns: (id: string, start_at?: string, end_at?: string) => {
      const p=new URLSearchParams();if(start_at)p.set('start_at',start_at);if(end_at)p.set('end_at',end_at);
      return request<Schemas['PatternList']>(`/seniors/${id}/patterns?${p}`);
    },
    pattern: (id: string) => request<Schemas['Pattern']>(`/patterns/${id}`),
    voices: () => request<Schemas['VoiceList']>('/voices'),
    medications: (id: string) => request<Medication[]>(`/seniors/${id}/medications`),
    // 같은 작업의 재시도에는 처음 만든 키를 그대로 전달하세요.
    createMedication: (id: string, body: MedicationCreate, idempotencyKey: string) => request<Medication>(`/seniors/${id}/medications`, {method:'POST',body,idempotencyKey}),
    updateMedication: (id: string, body: Schemas['MedicationPatch']) => request<Medication>(`/medications/${id}`, {method:'PATCH',body}),
    deleteMedication: (id: string) => request<void>(`/medications/${id}`, {method:'DELETE'}),
    medicationLogs: (id: string) => request<Schemas['MedicationLogList']>(`/seniors/${id}/medication-logs`),
    registerPush: (body: Schemas['PushTokenCreate']) => request<Schemas['PushTokenResult']>('/notifications/token', {method:'POST',body}),
    unregisterPush: (installationId: string) => request<void>(`/notifications/token/${installationId}`, {method:'DELETE'}),
    notifications: () => request<Schemas['NotificationList']>('/notifications'),
    sync: (id: string, cursor=0, limit=100) => request<Schemas['SyncResult']>(`/seniors/${id}/sync?after_cursor=${cursor}&limit=${limit}`),
    /** cursor는 사용자+어르신별로 저장합니다. 화면 해제 시 반환된 소켓을 닫으세요. */
    subscribe: (id: string, cursor: number, onEvent: (event: FeedEvent) => void, onCursor: (cursor: number) => void, onError: (message: string) => void) => {
      const ws = new WebSocket(`${root.replace(/^http/, 'ws')}/ws/live`);
      ws.onopen = () => ws.send(JSON.stringify({action:'SUBSCRIBE',access_token:getToken(),senior_id:id,after_cursor:cursor}));
      ws.onmessage = message => {
        const data = JSON.parse(message.data as string);
        if(data.type === 'EVENT') onEvent(data as FeedEvent);
        if(data.type === 'CURSOR') onCursor(data.cursor);
        if(data.type === 'ERROR') onError(`${data.error_code}: ${data.message}`);
      };
      return ws;
    }
  };
}
