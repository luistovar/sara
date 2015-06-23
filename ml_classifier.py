#!/usr/bin/env python
# coding=utf-8

from flask import Flask, request, jsonify, make_response, abort, g, url_for
from flask.ext.sqlalchemy import SQLAlchemy
from flask.ext.httpauth import HTTPBasicAuth
from passlib.apps import custom_app_context as pwd_context
from itsdangerous import (TimedJSONWebSignatureSerializer
                          as Serializer, BadSignature, SignatureExpired)
from sklearn.feature_extraction.text import TfidfVectorizer
from bs4 import BeautifulSoup
import re
import os
import sys
sys.path.append(os.path.join(os.path.dirname(__file__), 'DeepLearningMovies/'))
from KaggleWord2VecUtility import KaggleWord2VecUtility
sys.path.append(os.path.dirname(__file__))
from profanity_filter import *
from nltk.corpus import stopwords
import elasticsearch
from tasks import evaluate_petition


app = Flask(__name__)
app.config['SECRET_KEY'] = 'f@9^w9h9jkc-=jw7pl1eq!+^o*fmd@5o+7pc=0!00r0ef^(aqt'
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:////tmp/test.db'

db = SQLAlchemy(app)
auth = HTTPBasicAuth()


class User(db.Model):
    __tablename__ = 'users'
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(32), index=True)
    password_hash = db.Column(db.String(64))

    def hash_password(self, password):
        self.password_hash = pwd_context.encrypt(password)

    def verify_password(self, password):
        return pwd_context.verify(password, self.password_hash)

    def generate_auth_token(self, expiration=31556926):
        s = Serializer(app.config['SECRET_KEY'], expires_in=expiration)
        return s.dumps({'id': self.id})

    @staticmethod
    def verify_auth_token(token):
        s = Serializer(app.config['SECRET_KEY'])
        try:
            data = s.loads(token)
        except SignatureExpired:
            return None
        except BadSignature:
            return None
        user = User.query.get(data['id'])
        return user


@auth.verify_password
def verify_password(username_or_token, password):
    user = User.verify_auth_token(username_or_token)
    if not user:
        user = User.query.filter_by(username=username_or_token).first()
        if not user or not user.verify_password(password):
            return False
    g.user = user
    return True


@app.route('/users', methods=['POST'])
def new_user():
    username = request.json.get('username')
    password = request.json.get('password')
    if username is None or password is None:
        abort(400)
    if User.query.filter_by(username=username).first() is not None:
        abort(400)
    user = User(username=username)
    user.hash_password(password)
    db.session.add(user)
    db.session.commit()
    return (jsonify({'username': user.username}), 201,
            {'Location': url_for('get_user', id=user.id, _external=True)})



@app.route('/users/token')
@auth.login_required
def get_auth_token():
    token = g.user.generate_auth_token(31556926)
    return jsonify({'token': token.decode('ascii'), 'duration': 31556926})


def review_words(raw_text):
    review_text = BeautifulSoup(raw_text).get_text()
    letters_only = re.sub("^(\w+)[0-9]@", "", review_text)
    callback = lambda pat: pat.group(0).decode('utf-8').lower()
    iac = re.sub(u"Ă", u"í", letters_only)
    ene = re.sub(u"Ñ", u"ñ", iac)
    words = re.sub("(\w+)", callback, ene).split()
    stops = set(stopwords.words("spanish"))
    meaningful_words = [w for w in words if w not in stops]
    return u" ".join(meaningful_words)


def get_relevant_hits(like_text):
    index_name = "peticion"
    doc_type = "pregunta"
    stop_words = ["para", "apoyo", "una", "la", "el", "de", "en"]
    body = {"query": {"more_like_this": {"fields": ["titulo"],
            "like_text": like_text, "min_term_freq": 1,
            "max_query_terms": 100, "min_doc_freq": 0,
            "stop_words": stop_words}}}
    es = elasticsearch.Elasticsearch()
    mlts = es.search(index=index_name, doc_type=doc_type, body=body)
    relevant_sugestions = []
    hits = mlts.get('hits')["hits"]
    for hit in hits:
        if hit["_score"] >= 0.5:
            source = hit["_source"]
            relevant_sugestions.append({"title": source["titulo"],
                                        "description": source["sugerencia"],
                                        "link": source["link"]})
    return relevant_sugestions


@app.errorhandler(400)
def bad_request(error):
    app.logger.error(error)
    return make_response(jsonify({'error': 'Bad Request'}), 400)


@app.errorhandler(404)
def not_found(error):
    app.logger.error(error)
    return make_response(jsonify({'error': 'Not Found'}), 404)


@app.route('/sac/peticiones/filtro_malas_palabras', methods=['POST'])
def filtro_malas_palabras():
    f = ProfanitiesFilter(my_list, replacements="-")
    if not request.json or not 'descripcion' in request.json:
        abort(400)
    task = {
        'folioSAC': request.json['folioSAC'],
        'descripcion': request.json['descripcion'],
    }
    profanity_score = f.profanity_score(task['descripcion'])
    return jsonify({'indice_de_groserias': profanity_score})


@app.route('/petition/classification', methods=['POST'])
@auth.login_required
def create_task():
    if not request.json or not 'id' in request.json:
        abort(400)
    task = {
        'id': request.json['id'],
        'text': request.json['text'],
    }
    clean_test_descripciones = []
    features = review_words(task['text'])
    clean_test_descripciones.append(u" ".join(
        KaggleWord2VecUtility.review_to_wordlist(features, True)))
    myeval = evaluate_petition.delay(clean_test_descripciones)
    # tasks.append(task)
    return jsonify({'id': request.json['id'],
                    'text': request.json['text']}), 201


@app.route('/recommendations', methods=['GET'])
def get_hits():
    if not request.json or not 'title' in request.json:
        abort(400)
    task = {
        'title': request.json['title']
    }
    relevant_sugestions = get_relevant_hits(task['title'])
    return jsonify({'results': relevant_sugestions})


if __name__ == '__main__':
    if not os.path.exists('db.sqlite'):
        db.create_all()
    app.run(host='0.0.0.0', debug=True)
