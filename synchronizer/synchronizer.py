import logging
import os
import time

import boto3
import requests
from botocore.client import Config

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
logger = logging.getLogger(__name__)

S3_ENDPOINT_URL  = os.environ['S3_ENDPOINT_URL']
S3_BUCKET        = os.environ['S3_BUCKET']
S3_ACCESS_KEY    = os.environ['S3_ACCESS_KEY']
S3_SECRET_KEY    = os.environ['S3_SECRET_KEY']
DJANGO_API_URL   = os.environ.get('DJANGO_API_URL', 'http://backend:8000/api')
WORKER_API_TOKEN = os.environ.get('WORKER_API_TOKEN', '')
SYNC_INTERVAL    = int(os.environ.get('SYNC_INTERVAL', '60'))


def get_s3_client():
    return boto3.client(
        's3',
        endpoint_url=S3_ENDPOINT_URL,
        aws_access_key_id=S3_ACCESS_KEY,
        aws_secret_access_key=S3_SECRET_KEY,
        config=Config(signature_version='s3v4'),
        region_name='us-east-1',
    )


def list_downloads(s3):
    """Возвращает dict: {(streamer_slug, name_slug): [s3_url, ...]}"""
    groups = {}
    paginator = s3.get_paginator('list_objects_v2')
    for page in paginator.paginate(Bucket=S3_BUCKET, Prefix='downloads/'):
        for obj in page.get('Contents', []):
            key = obj['Key']
            parts = key.split('/')
            # downloads / streamer / name / video_N.ext
            if len(parts) < 4:
                continue
            _, streamer_slug, name_slug, filename = parts[0], parts[1], parts[2], parts[3]
            if not filename:
                continue
            url = f'{S3_ENDPOINT_URL}/{S3_BUCKET}/{key}'
            groups.setdefault((streamer_slug, name_slug), []).append(url)
    return groups


def upsert_film(streamer_slug: str, name_slug: str, s3_urls: list):
    title = name_slug.replace('_', ' ').replace('-', ' ').strip()
    slug  = name_slug.lower()

    headers = {'Authorization': f'Token {WORKER_API_TOKEN}'} if WORKER_API_TOKEN else {}
    payload = {
        'slug':    slug,
        'title':   title,
        's3_urls': sorted(s3_urls),
        'download_status': 'done',
    }
    try:
        resp = requests.post(f'{DJANGO_API_URL}/films/upsert/', json=payload, headers=headers, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        action = 'создан' if data.get('created') else 'обновлён'
        logger.info('Film "%s" %s (id=%s)', title, action, data.get('id'))
    except Exception as e:
        logger.warning('Не удалось upsert film "%s": %s', title, e)


def sync_once():
    logger.info('Начинаю синхронизацию S3 → Django...')
    s3 = get_s3_client()
    groups = list_downloads(s3)
    logger.info('Найдено групп в S3: %d', len(groups))
    for (streamer_slug, name_slug), urls in groups.items():
        upsert_film(streamer_slug, name_slug, urls)
    logger.info('Синхронизация завершена')


if __name__ == '__main__':
    logger.info('Synchronizer запущен, интервал %ds', SYNC_INTERVAL)
    while True:
        try:
            sync_once()
        except Exception as e:
            logger.error('Ошибка синхронизации: %s', e, exc_info=True)
        time.sleep(SYNC_INTERVAL)
