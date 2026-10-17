"""CDK app: S3 bucket + container-image Lambda that transcribes uploads.

Settings come from cdk.json "context" and can be overridden per deploy:
    cdk deploy -c model=turbo -c architecture=arm64
"""

from pathlib import Path

import aws_cdk as cdk
from aws_cdk import (
    Duration,
    RemovalPolicy,
    Size,
)
from aws_cdk import (
    aws_iam as iam,
)
from aws_cdk import (
    aws_lambda as lambda_,
)
from aws_cdk import (
    aws_lambda_destinations as destinations,
)
from aws_cdk import (
    aws_logs as logs,
)
from aws_cdk import (
    aws_s3 as s3,
)
from aws_cdk import (
    aws_s3_notifications as s3n,
)
from aws_cdk import (
    aws_sqs as sqs,
)
from aws_cdk.aws_ecr_assets import Platform
from constructs import Construct

CONTAINER_DIR = str(Path(__file__).resolve().parents[2] / "container")

ARCHITECTURES = {
    "x86_64": (lambda_.Architecture.X86_64, Platform.LINUX_AMD64),
    "arm64": (lambda_.Architecture.ARM_64, Platform.LINUX_ARM64),
}


class WhisperStack(cdk.Stack):
    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        def ctx(name):
            return self.node.try_get_context(name)

        input_prefix = ctx("inputPrefix")
        output_prefix = ctx("outputPrefix")
        architecture, platform = ARCHITECTURES[ctx("architecture")]
        prefixes_ok = (
            input_prefix.endswith("/")
            and output_prefix.endswith("/")
            and "/" not in (input_prefix, output_prefix)
            and not output_prefix.startswith(input_prefix)
            and not input_prefix.startswith(output_prefix)
        )
        if not prefixes_ok:
            # Transcripts written under the input prefix would re-trigger the function.
            raise ValueError("inputPrefix and outputPrefix must end in / and must not contain each other")

        bucket = s3.Bucket(
            self,
            "Bucket",
            bucket_name=ctx("bucketName") or None,
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            encryption=s3.BucketEncryption.S3_MANAGED,
            enforce_ssl=True,
            object_ownership=s3.ObjectOwnership.BUCKET_OWNER_ENFORCED,
            removal_policy=RemovalPolicy.RETAIN,
        )

        # Failed transcriptions (bad media, too long, timeouts) land here
        # instead of being retried.
        failures = sqs.Queue(
            self,
            "Failures",
            retention_period=Duration.days(14),
            encryption=sqs.QueueEncryption.SQS_MANAGED,
            enforce_ssl=True,
        )

        log_group = logs.LogGroup(
            self,
            "Logs",
            retention=logs.RetentionDays.ONE_MONTH,
            removal_policy=RemovalPolicy.DESTROY,
        )

        # An explicit role, so the function gets only the grants below rather
        # than the AWSLambdaBasicExecutionRole managed policy (logs on "*").
        role = iam.Role(self, "Role", assumed_by=iam.ServicePrincipal("lambda.amazonaws.com"))
        log_group.grant_write(role)

        reserved = ctx("reservedConcurrency")
        function = lambda_.DockerImageFunction(
            self,
            "Transcribe",
            code=lambda_.DockerImageCode.from_image_asset(
                CONTAINER_DIR,
                platform=platform,
                build_args={"WHISPER_MODEL": ctx("model")},
            ),
            architecture=architecture,
            role=role,
            memory_size=int(ctx("memoryMb")),
            ephemeral_storage_size=Size.mebibytes(int(ctx("ephemeralStorageMb"))),
            timeout=Duration.seconds(int(ctx("timeoutSeconds"))),
            # "" means no cap; 0 is a real value (it stops the function).
            reserved_concurrent_executions=None if reserved in ("", None) else int(reserved),
            retry_attempts=0,
            on_failure=destinations.SqsDestination(failures),
            log_group=log_group,
            environment={
                "INPUT_PREFIX": input_prefix,
                "OUTPUT_PREFIX": output_prefix,
                "ALLOWED_BUCKETS": bucket.bucket_name,
                "EXPECTED_BUCKET_OWNER": self.account,
                "WHISPER_LANGUAGE": ctx("language"),
                "OUTPUT_FORMATS": ctx("outputFormats"),
                "MAX_INPUT_MB": str(ctx("maxInputMb")),
                "MAX_AUDIO_SECONDS": str(ctx("maxAudioSeconds")),
            },
        )

        # Read only the input prefix, write only the output prefix.
        function.add_to_role_policy(
            iam.PolicyStatement(
                actions=["s3:GetObject", "s3:GetObjectVersion"],
                resources=[bucket.arn_for_objects(f"{input_prefix}*")],
            )
        )
        function.add_to_role_policy(
            iam.PolicyStatement(
                actions=["s3:PutObject"],
                resources=[bucket.arn_for_objects(f"{output_prefix}*")],
            )
        )

        # Only uploads under the input prefix trigger the function, so the
        # transcripts it writes never re-trigger it.
        bucket.add_event_notification(
            s3.EventType.OBJECT_CREATED,
            s3n.LambdaDestination(function),
            s3.NotificationKeyFilter(prefix=input_prefix),
        )

        cdk.CfnOutput(self, "BucketName", value=bucket.bucket_name)
        cdk.CfnOutput(self, "UploadTo", value=f"s3://{bucket.bucket_name}/{input_prefix}")
        cdk.CfnOutput(self, "TranscriptsAt", value=f"s3://{bucket.bucket_name}/{output_prefix}")
        cdk.CfnOutput(self, "FunctionName", value=function.function_name)
        cdk.CfnOutput(self, "FailureQueueUrl", value=failures.queue_url)


app = cdk.App()
WhisperStack(app, app.node.try_get_context("stackName"))
app.synth()
