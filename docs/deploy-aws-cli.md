# Deploy with the AWS CLI (no infrastructure tool)

Every resource created by hand, for anyone who'd rather not use CDK or Terraform, or wants to see exactly what is involved. The end result matches the other two options.

Requires the AWS CLI v2, Docker, `make` and `jq`.

## 0. Settings

```bash
export AWS_REGION=us-east-1
NAME=whisper
ACCOUNT=$(aws sts get-caller-identity --query Account --output text)
BUCKET=$NAME-$ACCOUNT-$AWS_REGION
```

## 1. Bucket

```bash
if [ "$AWS_REGION" = us-east-1 ]; then
  aws s3api create-bucket --bucket "$BUCKET"
else
  aws s3api create-bucket --bucket "$BUCKET" --create-bucket-configuration LocationConstraint="$AWS_REGION"
fi
aws s3api put-public-access-block --bucket "$BUCKET" --public-access-block-configuration \
  BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true
```

Refuse any request that isn't over TLS:

```bash
aws s3api put-bucket-policy --bucket "$BUCKET" --policy "{
  \"Version\": \"2012-10-17\",
  \"Statement\": [{\"Sid\": \"DenyInsecureTransport\", \"Effect\": \"Deny\", \"Principal\": \"*\", \"Action\": \"s3:*\",
    \"Resource\": [\"arn:aws:s3:::$BUCKET\", \"arn:aws:s3:::$BUCKET/*\"],
    \"Condition\": {\"Bool\": {\"aws:SecureTransport\": \"false\"}}}]
}"
```

New buckets are already encrypted (SSE-S3) and have ACLs disabled.

## 2. Image

```bash
aws ecr create-repository --repository-name "$NAME" --image-scanning-configuration scanOnPush=true
aws ecr put-lifecycle-policy --repository-name "$NAME" --lifecycle-policy-text '{"rules": [{
  "rulePriority": 1, "description": "Expire untagged images beyond the newest 10",
  "selection": {"tagStatus": "untagged", "countType": "imageCountMoreThan", "countNumber": 10},
  "action": {"type": "expire"}}]}'
make push REPO="$NAME"            # add ARCH=arm64 for Graviton, MODEL=turbo for another model
IMAGE=$ACCOUNT.dkr.ecr.$AWS_REGION.amazonaws.com/$NAME:latest
```

## 3. Failure queue and log group

```bash
QUEUE_URL=$(aws sqs create-queue --queue-name "$NAME-failures" \
  --attributes MessageRetentionPeriod=1209600,SqsManagedSseEnabled=true --query QueueUrl --output text)
QUEUE_ARN=$(aws sqs get-queue-attributes --queue-url "$QUEUE_URL" \
  --attribute-names QueueArn --query Attributes.QueueArn --output text)
aws sqs set-queue-attributes --queue-url "$QUEUE_URL" --attributes "{\"Policy\": $(jq -cn --arg arn "$QUEUE_ARN" '{
  Version: "2012-10-17",
  Statement: [{Sid: "DenyInsecureTransport", Effect: "Deny", Principal: "*", Action: "sqs:*",
    Resource: $arn, Condition: {Bool: {"aws:SecureTransport": "false"}}}]
} | tojson')}"

aws logs create-log-group --log-group-name "/aws/lambda/$NAME"
aws logs put-retention-policy --log-group-name "/aws/lambda/$NAME" --retention-in-days 30
```

## 4. Role

The function may read only `uploads/*`, write only `transcripts/*`, write its own logs and send failures to its queue.

```bash
aws iam create-role --role-name "$NAME-function" --assume-role-policy-document '{
  "Version": "2012-10-17",
  "Statement": [{"Effect": "Allow", "Principal": {"Service": "lambda.amazonaws.com"}, "Action": "sts:AssumeRole"}]
}'

aws iam put-role-policy --role-name "$NAME-function" --policy-name transcribe --policy-document "{
  \"Version\": \"2012-10-17\",
  \"Statement\": [
    {\"Effect\": \"Allow\", \"Action\": [\"s3:GetObject\", \"s3:GetObjectVersion\"], \"Resource\": \"arn:aws:s3:::$BUCKET/uploads/*\"},
    {\"Effect\": \"Allow\", \"Action\": \"s3:PutObject\", \"Resource\": \"arn:aws:s3:::$BUCKET/transcripts/*\"},
    {\"Effect\": \"Allow\", \"Action\": [\"logs:CreateLogStream\", \"logs:PutLogEvents\"],
     \"Resource\": \"arn:aws:logs:$AWS_REGION:$ACCOUNT:log-group:/aws/lambda/$NAME:*\"},
    {\"Effect\": \"Allow\", \"Action\": \"sqs:SendMessage\", \"Resource\": \"$QUEUE_ARN\"}
  ]
}"
```

## 5. Function

```bash
aws lambda create-function --function-name "$NAME" \
  --package-type Image --code ImageUri="$IMAGE" \
  --role "arn:aws:iam::$ACCOUNT:role/$NAME-function" \
  --architectures x86_64 \
  --memory-size 10240 --timeout 900 --ephemeral-storage Size=4096 \
  --environment "{\"Variables\": {
      \"ALLOWED_BUCKETS\": \"$BUCKET\",
      \"EXPECTED_BUCKET_OWNER\": \"$ACCOUNT\",
      \"OUTPUT_FORMATS\": \"txt,srt,vtt,json\",
      \"MAX_AUDIO_SECONDS\": \"3600\"
  }}"
aws lambda wait function-active-v2 --function-name "$NAME"

# At most 5 transcriptions at once, which also caps cost. Accounts with a total
# concurrency limit of 10 can't reserve any: skip this line there.
aws lambda put-function-concurrency --function-name "$NAME" --reserved-concurrent-executions 5

# No retries; failed events go to the queue.
aws lambda put-function-event-invoke-config --function-name "$NAME" \
  --maximum-retry-attempts 0 \
  --destination-config "{\"OnFailure\": {\"Destination\": \"$QUEUE_ARN\"}}"
```

If the role was created seconds earlier, `create-function` can fail with "The role defined for the function cannot be assumed by Lambda". Wait ten seconds and run it again.

Use `--architectures arm64` if you pushed with `ARCH=arm64`. Add `\"WHISPER_LANGUAGE\": \"en\"` and the other [settings](configuration.md#environment-variables) to the variables as needed.

## 6. Trigger

Allow this bucket (in this account only) to invoke the function, then send it uploads under `uploads/`:

```bash
aws lambda add-permission --function-name "$NAME" --statement-id s3-invoke \
  --action lambda:InvokeFunction --principal s3.amazonaws.com \
  --source-arn "arn:aws:s3:::$BUCKET" --source-account "$ACCOUNT"

aws s3api put-bucket-notification-configuration --bucket "$BUCKET" --notification-configuration "{
  \"LambdaFunctionConfigurations\": [{
    \"LambdaFunctionArn\": \"arn:aws:lambda:$AWS_REGION:$ACCOUNT:function:$NAME\",
    \"Events\": [\"s3:ObjectCreated:*\"],
    \"Filter\": {\"Key\": {\"FilterRules\": [{\"Name\": \"prefix\", \"Value\": \"uploads/\"}]}}
  }]
}"
```

The prefix filter matters: without it, every transcript the function writes would trigger it again.

## 7. Try it

```bash
aws s3 cp samples/recording.m4a "s3://$BUCKET/uploads/recording.m4a"
# Wait a minute, then:
aws s3 ls "s3://$BUCKET/transcripts/"
aws s3 cp "s3://$BUCKET/transcripts/recording.m4a.txt" -
```

Nothing showed up? Check the logs and the failure queue:

```bash
aws logs tail "/aws/lambda/$NAME" --since 10m
aws sqs receive-message --queue-url "$QUEUE_URL"
```

## Updating

```bash
make update-function FUNCTION="$NAME" REPO="$NAME"            # add ARCH=arm64 for a Graviton function
```

This rebuilds, pushes, points the function at the new image and waits for the update to finish. The `ARCH` must match the function's architecture.
