from flask import Flask, request, jsonify, abort
from flask_mail import Mail, Message
from flask_sqlalchemy import SQLAlchemy
from flask_restful import Resource, Api
from flask_swagger_ui import get_swaggerui_blueprint
import os
import yaml
from yaml import Loader, Dumper
import logging
import uuid
from datetime import datetime, timedelta
from generateApiKey import generateApiKey

app = Flask(__name__)
api = Api(app)

app_apisalt = os.environ.get('APP_APISALT', '')
db_user = os.environ.get('DB_USER', '')
db_password = os.environ.get('DB_PASSWORD', '')
db_name = os.environ.get('DB_NAME', '')
mail_server = os.environ.get('MAIL_SERVER', '')
mail_port = os.environ.get('MAIL_PORT', '')
mail_username = os.environ.get('MAIL_USERNAME', '')
mail_password = os.environ.get('MAIL_PASSWORD', '')

token_retention = 7 # days

# Configuration db
app.config['SQLALCHEMY_DATABASE_URI'] = 'postgresql://' + db_user + ':' + db_password + '@postgres:5432/' + db_name
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db = SQLAlchemy(app)

# Configuration mail
app.config['MAIL_SERVER'] = mail_server
app.config['MAIL_PORT'] = mail_port
app.config['MAIL_USERNAME'] = mail_username
app.config['MAIL_PASSWORD'] = mail_password
app.config['MAIL_USE_TLS'] = True
app.config['MAIL_USE_SSL'] = False
mail = Mail(app)

# Define a sample resource
class HelloWorld(Resource):
    def get(self):
        return jsonify({'message': 'Hello, World!'})

# Add the resource to the API
api.add_resource(HelloWorld, '/hello')

# Configure Swagger UI
SWAGGER_URL = '/swagger'
API_URL = 'https://api.schaakrating.nl/swagger.yaml'
API_URL = 'http://localhost:5000/swagger.yaml'
swaggerui_blueprint = get_swaggerui_blueprint(
    SWAGGER_URL,
    API_URL,
    config={
        'app_name': "Schaakrating API"
    }
)
app.register_blueprint(swaggerui_blueprint, url_prefix=SWAGGER_URL)

@app.route('/swagger.yaml')
def swagger():
    with open('swagger.yaml', 'r') as f:
        return jsonify(yaml.load(f, Loader=Loader))

class AppKeyRequest(db.Model):
    id = db.Column(db.String(36), primary_key=True, default=str(uuid.uuid4()), unique=True, nullable=False)
    app_name = db.Column(db.String(120), nullable=False)
    app_apikey = db.Column(db.String(64), unique=True, default=generateApiKey(app_apisalt, 15), nullable=False)
    status = db.Column(db.String(20), default='active')
   
class KeyRequest(db.Model):
    id = db.Column(db.String(36), primary_key=True, default=str(uuid.uuid4()), unique=True, nullable=False)
    email = db.Column(db.String(120), nullable=False)
    app_name = db.Column(db.String(120), nullable=False)
    api_key = db.Column(db.String(64), unique=True, default=generateApiKey(app_apisalt, 15), nullable=False)
    status = db.Column(db.String(20), default='pending')

class ConfirmToken(db.Model):
    id = db.Column(db.String(36), primary_key=True, default=str(uuid.uuid4()), unique=True, nullable=False)
    email = db.Column(db.String(120), nullable=False)
    token = db.Column(db.String(64), unique=True, default=generateApiKey(app_apisalt, 15, "IJCO_UI_prod"), nullable=False)
    timestamp = db.Column(db.DateTime(timezone=True), default=datetime.utcnow(), nullable=False)

def check_defaultapp():
    appkeys = (db.session.query(AppKeyRequest)
        .filter(AppKeyRequest.app_name == 'IJSCO_UI')
        .all())
    appkey_no = len(appkeys)
    if (appkey_no > 1):
        logging.error('More then one record in AppKeyRequest for IJSCO_UI is ' + str(appkey_no))
        return False
    if (appkey_no == 0):
        logging.warning('Number of records in AppKeyRequest for IJSCO_UI is ' + str(appkey_no) + '.\nCreating new appkey for IJSCO_UI')
        new_appkey = AppKeyRequest(app_name='IJSCO_UI')
        db.session.add(new_appkey)
        db.session.commit()
        logging.warning('New app_apikey for IJSCO_UI is ' + str(new_appkey.app_apikey) + '.')
        return True
    if (appkey_no == 1):
        logging.warning('Appkey for IJSCO_UI is ' + appkeys[0].app_apikey)
        return True

def cleanup_oldtokens():
#    target_time_for_batch = datetime.utcnow() - timedelta(days={token_retention})
    target_time_for_batch = datetime.utcnow() - timedelta(days = 7)
    timedout = (db.session.query(ConfirmToken)
                    .filter(ConfirmToken.timestamp < target_time_for_batch)
                    .all())
    try:
        db.session.delete(timedout)
        db.session.commit()
    except Exception as e:
        print (str(e))

def authenticate_request_by_apikey():
    logging.warning("authenticate_request_by_apikey - Entered")
    provided_app_apikey = False
    logging.warning("Authenticate API KEY - " + str(request))
    logging.warning('Trying to retrieve request')
    logging.warning(request.json.get('app_apikey'))
    try:
        logging.warning("app_apikey = " + provided_app_apikey)
    except Exception as e:
        logging.error('Could not retrieve app_apikey in request')
    if not provided_app_apikey:
        abort(401, 'No application api key.')
    if not validate_apikey(provided_app_apikey):
        abort(401, 'No valid application api key.')

def validate_app_apikey(provided_appname, provided_apikey):
    logging.warning('var =' + app_apikey)
    apikeys = db.session.query(AppKeyRequest).filter(AppKeyRequest.app_name == provided_appname).filter(AppKeyRequest.apikey == provided_apikey).all()
    if (len(apikeys)>1):
        logging.error('More then one record in AppKeyRequest for ' + provided_appname)
    if (len(apikeys)==1):                       
        return True
    
def validate_api_key(provided_app_key, provided_key):
    api_key_record = KeyRequest.query.filter_by(app_apikey=provided_app_key, api_key=provided_key).first()
    return api_key_record is not None

def send_email(email, subject, body):
    logging.warning('Sending email')
    try:
        message = Message(subject=subject, sender='no-reply@schaakrating.nl', recipients=[email])
        message.body = body
        mail.send(message)
        return jsonify({'message': "Email sent succesfully"}), 200
    except Exception as e:
        logging.warning('Problem sending email')
        return jsonify({'error': str(e)}), 500
        
@app.route('/private-key-request', methods=['POST','GET'])
def private_api_key_request():
    cleanup_oldtokens()
    logging.warning('old tokens removed')
    validate_app_apikey(request.json.get('app_name'), request.json.get('app_apikey'))

    logging.warning("API KEY present")
    email = request.json.get('email')
    new_keyrequest = KeyRequest(email=email)
    
    db.session.add(new_keyrequest)
    db.session.commit()

    logging.warning("Created apikey is " + new_keyrequest.api_key)

    new_token = ConfirmToken(email=email)

    db.session.add(new_token)
    db.session.commit()
    
    
    # Send confirmation email
    # ...
    # sendmail with token new.token
    subject = "API key for IJSCO_UI (productie)"
    link = "https://api.schaakrating.nl/private-key-confirm?email=" + email + "&token=" + new_token.token
    body = "Welkom,\n\nEr is voor u een API Key aangemaakt voor de applicatie IJSCO_UI. Deze API key is gebonden aan uw e-mail adres.\n\nOm uw API key te activeren dient u de volgende link te volgen.\n\n" + link + "\n\nHet Schaakrating.nl team bedankt je voor interesse en wenst je veel succes."
    return send_email(email, subject, body)
#    return send_email(email, new_token.token)
# Add the resource to the API
#api.add_resource(private_key_request, '/private_key_request')

def validate_token(provided_email, provided_token):
    token_record = ConfirmToken.query.filter_by(email=provided_email, token=provided_token).first()
    return (token_record is not None)

@app.route('/private-key-confirm', methods=['GET'])
def private_key_confirm():
    logging.warning('Private key confirmation')
    provided_email = request.args['email']
    provided_token = request.args['token']
    logging.warning('Email is' + provided_email)
    logging.warning('Token is' + provided_token)
    token_record = validate_token(provided_email, provided_token)
    if (not(token_record)):
        return jsonify({'error': 'Token is invalid or expired.'}), 500
    key_record = KeyRequest.query.filter_by(email=provided_email).first()
    if ((key_record) is None):
        return jsonify({'error': 'Key not found expired.'}), 500
    key_record.status = "Active"
    db.session.commit()
    subject = "API key for IJSCO_UI (productie)"
    body = body = "Welkom,\n\nU heeft een API Key geactiveerd voor de applicatie IJSCO_UI. Deze API key is gebonden aan uw e-mail adres.\n\nOm uw API key te gebruiken die u deze key **" + key_record.api_key + "** in de applicatie in te voeren.\n\nHet Schaakrating.nl team bedankt je voor interesse en wenst je veel succes."
    return send_email(provided_email, subject, body)
#    return jsonify({"message": "Private key activated."})

with app.app_context():
    if __name__ == '__main__':
#        print (db_user)
#        print (db_password)

        db.create_all()
        logging.warning('Now starting run')
        check_defaultapp()
        app.run(host='0.0.0.0', port=5000, debug=True)    
    