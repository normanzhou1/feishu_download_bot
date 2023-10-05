from flask import Flask, request, jsonify
import logging
import requests
from requests_toolbelt import MultipartEncoder
import json
import os
import re
import subprocess
import threading
import queue
import argparse

LOG_FORMAT = "%(asctime)s %(message)s " #配置输出日志格式
DATE_FORMAT = '%Y-%m-%d %H:%M:%S'  #配置输出时间的格式，注意月份和天数不要搞乱了
logging.basicConfig(level=logging.DEBUG,
                    format=LOG_FORMAT,
                    datefmt = DATE_FORMAT ,
                    filename=r"/var/log/feishu.log"  #有了filename参数就不会直接输出显示到控制台，而是直接写入文件
                    )

app = Flask(__name__)
@app.route('/feishu_download', methods=['POST'])
def webhook():
    json_data = request.json
    #print(json_data)
    if 'challenge' in json_data:
        logging.info("[INFO]Received challenge")
        return jsonify({'challenge':json_data['challenge']})
    if 'event' in json_data and 'message' in json_data['event']:
        return jsonify(webhook_message(json_data['event']['message']))
    logging.error(f'[ERROR]Received unknown webhook: {json_data}')
    return jsonify({})

def webhook_message(message):
    chat_id = message['chat_id']
    content = json.loads(message['content'])
    text = content['text']
    match = re.search(r'(https://www\.youtube\.com/watch\?v\=[a-zA-Z0-9\_\-]+)', text)
    if match:
        url = match[1]
        #print(chat_id, url)
        logging.info(f'[INFO]Chat_id={chat_id}, url={url}')
        message_queue.put([chat_id, url])
    else:
        logging.error(f'[ERROR]Received unexpected message:{message}')
    #print(text, chat_id)
    return {}

def old_webhook():
    input_data = request.data.decode('utf-8')    
    datas = input_data.split('\n')
    #logging.info(f'[INFO]Received: {input_data}')
    if not len(datas) >= 2:
        logging.error('[ERROR]Bad received')
        return jsonify({'message': 'Fail to receive POST'})
    
    sender_id, message = datas
    logging.info("[INFO]sender_id=" + sender_id + ", message=" + message)
    return jsonify({'message': 'Received POST request successfully'})

def run_web(ip, port):
    app.run(host=ip, port=port)

def get_access_code(id, secret):
    url = 'https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal'
    send = {
    "app_id": id,
    "app_secret": secret
    }
    headers = {
    'Content-Type': 'application/json; charset=utf-8'
    }
    response = requests.post(url, json=send, headers=headers)
    if response.status_code != 200:
        raise AssertionError(f"Http request error, http_code={response.status_code}")
    # 解析 JSON 响应
    response_data = response.json()
    if response_data['code'] != 0:
        raise AssertionError(f"Get access code error, code={response_data['code']}")
    return response_data['tenant_access_token']

def get_chat_id(access_code):
    url = "https://open.feishu.cn/open-apis/im/v1/chats?page_size=20"
    payload = ''
    headers = {
    'Authorization': f"Bearer {access_code}"
    }
    response = requests.request("GET", url, headers=headers, data=payload)
    response_data = response.json()
    if response.status_code != 200 or response_data['code'] != 0:
        raise AssertionError(f"Get chat id error, http_code={response.status_code} code={response_data['code']}")
    return response_data['data']['items'][0]['chat_id']

def post_text_message(access_code, chat_id, message):
    url = 'https://open.feishu.cn/open-apis/im/v1/messages?receive_id_type=chat_id'
    send = {
    "receive_id": chat_id,
    "msg_type": "text",
    "content": '{"text":"' + message + '"}',
    }
    headers = {
    'Authorization': f"Bearer {access_code}",
    'Content-Type': 'application/json; charset=utf-8'
    }
    response = requests.post(url, json=send, headers=headers)
    response_data = response.json()
    if response.status_code != 200 or response_data['code'] != 0:
        raise AssertionError(f"Post text message error, http_code={response.status_code} code={response_data['code']}")

def post_file_message(access_code, chat_id, file_key):
    url = 'https://open.feishu.cn/open-apis/im/v1/messages?receive_id_type=chat_id'
    send = {
    "receive_id": chat_id,
    "msg_type": "media",
    "content": '{"file_key":"' + file_key + '"}',
    }
    headers = {
    'Authorization': f"Bearer {access_code}",
    'Content-Type': 'application/json; charset=utf-8'
    }
    response = requests.post(url, json=send, headers=headers)
    response_data = response.json()
    if response.status_code != 200 or response_data['code'] != 0:
        raise AssertionError(f"Post file message error, http_code={response.status_code} code={response_data['code']}")
    
def upload_file(access_code, file_name):
    url = "https://open.feishu.cn/open-apis/im/v1/files"
    form = {'file_type': 'mp4',
            'file_name': file_name,
            'file':  (file_name, open(file_name,'rb'), 'video/mp4')} 
    multi_form = MultipartEncoder(form)
    headers = {
        'Authorization': f"Bearer {access_code}",
    }
    headers['Content-Type'] = multi_form.content_type
    response = requests.request("POST", url, headers=headers, data=multi_form)
    response_data = response.json()
    if response.status_code != 200 or response_data['code'] != 0:
        raise AssertionError(f"Upload file error, http_code={response.status_code} code={response_data['code']}")
    return response_data['data']['file_key']

def do_download(URL, access_code, chat_id):
    try:
        os.remove('download.mp4')
    except:
        pass

    args = ['yt-dlp_linux', '--max-filesize', '28M', '--output', 'download.mp4', URL]
    logging.info(f'[INFO]Start downloading {URL}')
    subprocess.run(args, stdout=subprocess.PIPE)

    if not os.path.exists('download.mp4') or os.path.getsize('download.mp4') == 0:
        post_text_message(access_code, chat_id, f'{URL}下载失败')
        raise AssertionError(f"Download file error, URL={URL}")

def run_once(URL, chat_id, app_id, app_secret):
    try:
        access_code = get_access_code(app_id, app_secret)
        logging.info(f'[INFO]access_code={access_code}')
        #chat_id = get_chat_id(access_code)
        #logging.info(f'[INFO]chat_id={chat_id}')
        post_text_message(access_code, chat_id, f'开始下载{URL}')
        do_download(URL, access_code, chat_id)
        file_key = upload_file(access_code, 'download.mp4')
        logging.info(f'[INFO]file_key={file_key}')
        post_file_message(access_code, chat_id, file_key)
    except Exception as e:
        logging.error(f'[ERROR]{e}')

def download_thread(queue, app_id, app_secret):
    while True:
        message = queue.get()
        chat_id = message[0]
        url = message[1]
        run_once(url, chat_id, app_id, app_secret)

#run_once('https://www.youtube.com/watch?v=biF1uCiCPBs')

parser = argparse.ArgumentParser(description="飞书群YouTube视频抓取器,四个参数为IP地址,端口,App ID和App Secret,具体请见飞书文档")
parser.add_argument('--ip', type=str, help='IP地址', required=True)
parser.add_argument('--port', type=int, help='端口,仅限一个', required=True)
parser.add_argument('--id', type=str, help='应用的App ID,具体见飞书文档', required=True)
parser.add_argument('--secret', type=str, help='应用的App Secret,具体见飞书文档', required=True)

args = parser.parse_args()
#try:
ip = args.ip
port = args.port
id = args.id
secret = args.secret
#except:
    #print(help_message)
#else:
message_queue = queue.Queue()
thread = threading.Thread(target=download_thread, args=(message_queue, id, secret))
thread.start()
run_web(ip, port)