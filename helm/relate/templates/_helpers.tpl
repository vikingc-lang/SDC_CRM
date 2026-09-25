{{- define "relate.fullname" -}}{{ printf "%s" .Release.Name | trunc 50 | trimSuffix "-" }}{{- end -}}
{{- define "relate.labels" -}}
app.kubernetes.io/part-of: relate
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
helm.sh/chart: {{ .Chart.Name }}-{{ .Chart.Version }}
{{- end -}}
{{- define "relate.selector" -}}
app.kubernetes.io/instance: {{ .root.Release.Name }}
app.kubernetes.io/component: {{ .component }}
{{- end -}}
{{- define "relate.image" -}}
{{- $reg := .root.Values.image.registry -}}
{{- if $reg }}{{ printf "%s/%s:%s" $reg .name .root.Values.image.tag }}{{ else }}{{ printf "%s:%s" .name .root.Values.image.tag }}{{ end -}}
{{- end -}}
{{- define "relate.secretName" -}}{{ .Values.secrets.existingSecret | default (printf "%s-secrets" (include "relate.fullname" .)) }}{{- end -}}
{{- define "relate.dbHost" -}}{{ if .Values.postgresql.enabled }}{{ include "relate.fullname" . }}-postgres{{ else }}{{ .Values.externalDatabase.host }}{{ end }}{{- end -}}
{{- define "relate.redisUrl" -}}{{ if .Values.redis.enabled }}redis://{{ include "relate.fullname" . }}-redis:6379/0{{ else }}{{ .Values.externalRedisUrl }}{{ end }}{{- end -}}
{{/* Environment shared by api and worker */}}
{{- define "relate.backendEnv" -}}
- name: POSTGRES_PASSWORD
  valueFrom: { secretKeyRef: { name: {{ include "relate.secretName" . }}, key: POSTGRES_PASSWORD } }
- name: DATABASE_URL
  value: "postgresql+asyncpg://{{ .Values.externalDatabase.user }}:$(POSTGRES_PASSWORD)@{{ include "relate.dbHost" . }}:{{ .Values.externalDatabase.port }}/{{ .Values.externalDatabase.database }}"
- name: JWT_SECRET
  valueFrom: { secretKeyRef: { name: {{ include "relate.secretName" . }}, key: JWT_SECRET } }
- name: DATA_ENCRYPTION_KEY
  valueFrom: { secretKeyRef: { name: {{ include "relate.secretName" . }}, key: DATA_ENCRYPTION_KEY } }
- name: ERP_REST_TOKEN
  valueFrom: { secretKeyRef: { name: {{ include "relate.secretName" . }}, key: ERP_REST_TOKEN, optional: true } }
- name: REDIS_URL
  value: {{ include "relate.redisUrl" . | quote }}
- name: OLLAMA_ENDPOINT
  value: "http://{{ include "relate.fullname" . }}-ollama:11434"
- name: WHISPER_ENDPOINT
  value: "http://{{ include "relate.fullname" . }}-whisper:9000"
- name: CORS_ORIGINS
  value: {{ .Values.publicWebUrl | quote }}
- name: PUBLIC_WEB_URL
  value: {{ .Values.publicWebUrl | quote }}
- name: STORAGE_DIR
  value: /data/storage
- name: ERP_EXCHANGE_DIR
  value: /data/erp-exchange
- name: USE_CELERY
  value: "true"
{{- range $k, $v := .Values.config }}
- name: {{ $k }}
  value: {{ $v | quote }}
{{- end }}
{{- end -}}
