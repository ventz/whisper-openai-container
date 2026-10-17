variable "region" {
  type    = string
  default = "us-east-1"
}

variable "name" {
  description = "Name for the function, ECR repository, role and queue."
  type        = string
  default     = "whisper"
}

variable "bucket_name" {
  description = "Bucket to create. Empty means <name>-<account>-<region>."
  type        = string
  default     = ""
}

variable "image_tag" {
  type    = string
  default = "latest"
}

variable "architecture" {
  description = "x86_64 or arm64; must match the image you pushed."
  type        = string
  default     = "x86_64"
  validation {
    condition     = contains(["x86_64", "arm64"], var.architecture)
    error_message = "architecture must be x86_64 or arm64."
  }
}

variable "input_prefix" {
  type    = string
  default = "uploads/"
}

variable "output_prefix" {
  type    = string
  default = "transcripts/"
}

variable "memory_mb" {
  description = "Memory also sets CPU: 10240 MB gives 6 vCPUs."
  type        = number
  default     = 10240
}

variable "ephemeral_storage_mb" {
  type    = number
  default = 4096
}

variable "timeout_seconds" {
  type    = number
  default = 900
}

variable "reserved_concurrency" {
  description = "Caps parallel transcriptions (and cost). -1 means no cap."
  type        = number
  default     = 5
}

variable "language" {
  description = "Language code such as en. Empty means auto-detect."
  type        = string
  default     = ""
}

variable "output_formats" {
  type    = string
  default = "txt,srt,vtt,json"
}

variable "max_input_mb" {
  type    = number
  default = 1024
}

variable "max_audio_seconds" {
  description = "Reject longer recordings before transcribing them. 0 means no limit (the function timeout still applies)."
  type        = number
  default     = 3600
}
