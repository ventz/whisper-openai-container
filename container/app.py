import os
import shutil
import json
import urllib3
import urllib.parse
import whisper
import torch
import boto3


s3 = boto3.client("s3")


def handler(event, context):
    try:
        #print("Received event: " + json.dumps(event, indent=2))
        bucket = event["Records"][0]["s3"]["bucket"]["name"]
        key = urllib.parse.unquote_plus(event['Records'][0]['s3']['object']['key'], encoding='utf-8')
        #print("Bucket:", bucket, "key:", key)
        os.makedirs("/tmp/data", exist_ok=True)
        os.chdir('/tmp/data')

        audio_file=f"/tmp/data/{key}"
        # Downloading file to transcribe
        s3.download_file(bucket, key, audio_file)

        # GPU!! (if available)
        device = "cuda" if torch.cuda.is_available() else "cpu"
        model = whisper.load_model("medium", download_root="/usr/local").to(device)
        #model = whisper.load_model("medium")
        #result = model.transcribe(audio_file, fp16=False, language='English', verbose=True)
        result = model.transcribe(audio_file, fp16=False, language='English')
        #print(s['text'].strip())

        object = s3.put_object(Bucket=bucket, Key=key+'.text', Body=result["text"].strip())

        try:
            # Generate a pre-signed URL for the S3 object
            expiration = 3600  # URL expiration time in seconds
            response = s3.generate_presigned_url('get_object',
                                                    Params={'Bucket': bucket, 'Key': key+'.text'},
                                                    ExpiresIn=expiration)

            output = f"Transcribed: {key}.text - {response}"

        except ClientError as e:
            print(e)

        return {
            "statusCode": 200,
            "body": json.dumps(output)
        }
    except Exception as e:
        print(e)
        return {
            "statusCode": 500,
            "body": json.dumps("Error processing the file")
        }
