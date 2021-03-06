from flask import Flask
from flask import render_template, make_response, jsonify, request, json
from flask_cors import CORS, cross_origin
from io import BytesIO
from urllib.parse import urlparse

import aws.s3client as s3
import boto3
import util.constants as c
import util.aws_config as conf
import util.regex as regex
import contextlib
import coco.coco as coco 
import os
import re
import requests
import sys
import ssl
import shutil
import tempfile
import urllib


app = Flask(__name__)


@app.route("/internal/health_check")
def health_check():
    return "ok\n"


@app.route("/via-style.css")
def stylesheet():
   return app.send_static_file('viae-style.css')


@app.route('/')
def index():
    return make_response(render_template('viae.html'))


@app.route('/images/in_progress', methods=['GET', 'POST'])
def images_in_progress():
    if request.method == 'POST':
        is_url = request.args.get("url")
        if is_url == 'true':
            urls = json.loads(request.data)
            url_list = urls.split()
            files_with_coco = []
            for u in url_list:
                url = urllib.parse.unquote(u)
                if url.startswith('https'):
                    ctx = ssl.create_default_context()
                else:
                    ctx = None
                with contextlib.closing(urllib.request.urlopen(url, context=ctx)) as f:
                    f_bytes = f.read()
                img_id = s3.inc_image_id()
                file_stem = regex.remove_prefix(url)
                fname = f'{img_id}-{file_stem}'
                coco_fname = regex.to_json(fname)

                s3.upload_image(BytesIO(f_bytes), conf.BUCKET, fname)
                urllib.request.urlcleanup()

                img_url = f'{c.IN_PROGRESS_IMAGES}{fname}'
                
                coco_obj = coco.setup_coco(img_id, img_url, fname, coco_fname, sys.getsizeof(f_bytes))
                s3.upload_coco(coco_fname)
                files_with_coco.append({'image_url': img_url, 'coco': coco_obj})
            return jsonify(files_with_coco)
        else:   
            files = request.files
            files_with_coco = []
            for i in range(len(files)):
                f = files[f'file_{i}']
                f_bytes = f.read()
                img_id = s3.inc_image_id()
                fname = f'{img_id}-{f.filename}'
                coco_fname = regex.to_json(fname)
                img_url = f'{c.IN_PROGRESS_IMAGES}{fname}'
                s3.upload_image(BytesIO(f_bytes), conf.BUCKET, fname)
                coco_obj = coco.setup_coco(img_id, img_url, fname, coco_fname, sys.getsizeof(f_bytes))
                s3.upload_coco(coco_fname)
                files_with_coco.append({'image_url': img_url, 'coco': coco_obj})
            return jsonify(files_with_coco)
    # GET
    coco_urls = s3.list_urls(conf.BUCKET, c.PROGRESS_COCO)
    via_annot_data = {}
    for url in coco_urls:
        with contextlib.closing(urllib.request.urlopen(url)) as coco_file:
            coco_bytes = coco_file.read()
        coco_obj = json.loads(coco_bytes)
        via_dict = coco.coco2via(coco_obj)
        fname = list(via_dict)[0]
        annot_data = via_dict.get(fname)
        via_annot_data[fname] = annot_data
    return jsonify(via_annot_data)


@app.route('/images/in_progress/<image_id>', methods=['PUT', 'POST'])
def submit_data(image_id):
    # save partial labeling data to s3 in_progress 
    if request.method == 'PUT': 
        via_label_data = json.loads(request.data)
        img_url = via_label_data['filename']
        fname = regex.remove_prefix(img_url)
        coco_fname = regex.to_json(fname)
        coco_obj = coco.via_to_coco(via_label_data, coco_fname)
        s3.upload_file(f'{c.tmp}{coco_fname}', f'in_progress_data/coco/{coco_fname}')

        via_annot_data = {}
        via_dict = coco.coco2via(coco_obj)
        fname = list(via_dict)[0]
        annot_data = via_dict.get(fname)
        via_annot_data[fname] = annot_data
        return jsonify(via_annot_data)

    # POST: move labeling data to s3 validate_data
    via_label_data = json.loads(request.data)
    img_url = via_label_data['filename']
    destination = via_label_data['destination']
    img_fname = regex.remove_prefix(img_url)
    coco_fname = regex.to_json(img_fname)
    coco_obj = coco.via_to_coco(via_label_data, coco_fname)
    s3.upload_file(f'{c.tmp}{coco_fname}', f'in_progress_data/coco/{coco_fname}')

    s3.move_to_destination('coco', coco_fname, destination)
    s3.move_to_destination('images', img_fname, destination)

    via_annot_data = {}
    via_dict = coco.coco2via(coco_obj)
    fname = list(via_dict)[0]
    annot_data = via_dict.get(fname)
    via_annot_data[fname] = annot_data
    return jsonify(via_annot_data)


@app.route('/images/in_progress/<image_id>', methods=['DELETE'])
def delete_data(image_id):
    img_url = json.loads(request.data)
    # delete coco and image from in progress
    img_fname = regex.remove_prefix(img_url)
    coco_fname = regex.to_json(img_fname)
    s3.delete_file('images', img_fname)
    s3.delete_file('coco', coco_fname)
    return jsonify({'image_url': img_url, 'coco': coco_fname})


if __name__ == "__main__":
    app.run(host='0.0.0.0', debug=True)
