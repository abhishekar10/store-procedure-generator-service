output "trigger_topic_arn" {
  description = "Point a budget notification here to arm the kill switch."
  value       = aws_sns_topic.trigger.arn
}

output "function_name" {
  value = aws_lambda_function.this.function_name
}
