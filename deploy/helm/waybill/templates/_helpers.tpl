{{- define "waybill.name" -}}
waybill
{{- end -}}

{{- define "waybill.labels" -}}
app.kubernetes.io/name: {{ include "waybill.name" . }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
helm.sh/chart: {{ .Chart.Name }}-{{ .Chart.Version }}
{{- end -}}

{{- define "waybill.databaseUrl" -}}
{{- if .Values.postgres.enabled -}}
postgresql+psycopg://{{ .Values.postgres.user }}:{{ .Values.postgres.password }}@waybill-postgres:5432/{{ .Values.postgres.database }}
{{- else -}}
{{ .Values.database.url }}
{{- end -}}
{{- end -}}
