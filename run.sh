#!/bin/bash
docker rm -f ventz/whisper

# There are 2 ways to run/interact with this:

# 1.) As a "regular container":
#   docker exec -it ventz/whisper /bin/bash"
#
#   From there, you can just run 'whisper', ex:
#   whisper 'recording.mp4' --language English --model base --fp16 False


# or


# 2.) As a Lambda functions that's container backed:
#   The idea is that you will setup a S3 bucket with a hook that calls
#   this Lambda when a new object is created or dropped.
#
#   This involves:
#   a.) Tagging the local docker image and pushing it to ECR:
#       docker tag ventz/whisper:latest 123456789012.dkr.ecr.us-east-1.amazonaws.com/whisper:latest
#       docker push 123456789012.dkr.ecr.us-east-1.amazonaws.com/whisper:latest
#   b.) Deploying a new Lambda function from ECR:
#       aws lambda create-function --region us-east-1 --function-name transcribe \
#           --package-type Image  \
#           --code ImageUri=<ECR Image URI>   \
#           --role  arn:aws:iam::123456789012:role/service-role/transcribe
#
#       NOTE: The role needs to have:
#           i.) AWSLambdaBasicExecutionRole (for: 'logs:CreateLogStream', and 'logs:PutLogEvents')
#           {
#               "Version": "2012-10-17",
#               "Statement": [
#                   {
#                       "Effect": "Allow",
#                       "Action": "logs:CreateLogGroup",
#                       "Resource": "arn:aws:logs:us-east-1:123456789012:*"
#                   },
#                   {
#                       "Effect": "Allow",
#                       "Action": [
#                           "logs:CreateLogStream",
#                           "logs:PutLogEvents"
#                       ],
#                       "Resource": [
#                           "arn:aws:logs:us-east-1:123456789012:log-group:/aws/lambda/transcribe:*"
#                       ]
#                   }
#               ]
#           }
#
#           and
#
#           ii.) Write access to S3 bucket:
#           {
#                "Version": "2012-10-17",
#                "Statement": [
#                    {
#                        "Sid": "VisualEditor0",
#                        "Effect": "Allow",
#                        "Action": [
#                            "s3:PutObject",
#                            "s3:GetObject",
#                            "s3:ListBucket"
#                        ],
#                        "Resource": "arn:aws:s3:::<YOUR BUCKET NAME>/*"
#                    }
#                ]
#            }
#
#   c.) Update code if you ever re-configure/re-build your container/Dockerfile:
#       aws lambda update-function-code --function-name transcribe --image-uri $(aws lambda get-function -- function-name transcribe | jq -r '.Code.ImageUri')
#
#       You can check when done with:
#       while [ "$(aws lambda get-function --function-name transcribe | jq -r '.Configuration.              LastUpdateStatus')" != "Successful" ]; do
#           sleep 1
#       done


#   MANUALLY TESTING THE LAMBDA LOCALLY:
#   docker run -it --rm -d -p 9000:8080 --name whisper ventz/whisper
#   and then
#   curl -XPOST "http://localhost:9000/2015-03-31/functions/function/invocations" -d @test-s3-json
#
#   NOTE: This is a "fake" event just to make sure you can locally run
#   the lambda. You will need a real s3 bucket and real file/recording + IAM permissions(see test-s3-json)
