output "repository_url" {
  value = aws_ecr_repository.this.repository_url
}

output "bucket_name" {
  value = aws_s3_bucket.this.bucket
}

output "upload_to" {
  value = "s3://${aws_s3_bucket.this.bucket}/${var.input_prefix}"
}

output "transcripts_at" {
  value = "s3://${aws_s3_bucket.this.bucket}/${var.output_prefix}"
}

output "function_name" {
  value = aws_lambda_function.this.function_name
}

output "failure_queue_url" {
  value = aws_sqs_queue.failures.url
}

# Read by `make tf-deploy` to push to the right place.
output "repository_name" {
  value = aws_ecr_repository.this.name
}

# depends_on makes the targeted first apply in `make tf-deploy` write these.
output "region" {
  value      = var.region
  depends_on = [aws_ecr_repository.this]
}

output "architecture" {
  value      = var.architecture
  depends_on = [aws_ecr_repository.this]
}

output "image_tag" {
  value      = var.image_tag
  depends_on = [aws_ecr_repository.this]
}
