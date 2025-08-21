import os
import json
import hashlib
import datetime
from flask import Flask, render_template, request, jsonify, redirect, url_for, session
from flask_bcrypt import Bcrypt
from pymongo import MongoClient
from bson import ObjectId
import jwt
from functools import wraps
from bson.json_util import dumps, loads

app = Flask(__name__, template_folder='interface')
app.secret_key = os.environ.get('JWT_SECRET_KEY', 'fallback-secret-key')

# Initialize Bcrypt
bcrypt = Bcrypt(app)

# MongoDB connection
mongo_uri = os.environ.get('MONGO_URI', 'mongodb://localhost:27017/skillforgex')
client = MongoClient(mongo_uri)
db = client.skillforgex

# Collections
users_collection = db.users
content_collection = db.content
blocks_collection = db.blocks
reputation_collection = db.reputation

# JWT Secret Key
JWT_SECRET_KEY = os.environ.get('JWT_SECRET_KEY')

# Custom JSON encoder to handle ObjectId
class JSONEncoder(json.JSONEncoder):
    def default(self, o):
        if isinstance(o, ObjectId):
            return str(o)
        if isinstance(o, datetime.datetime):
            return o.isoformat()
        return json.JSONEncoder.default(self, o)

app.json_encoder = JSONEncoder

# Authentication decorator
def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session:
            return jsonify({'error': 'Authentication required'}), 401
        return f(*args, **kwargs)
    return decorated_function

# Blockchain implementation
class ContentChain:
    def __init__(self):
        self.chain = []
        self.current_content = []
        self.difficulty = 2  # For PoW
        
        # Create genesis block if chain is empty
        if blocks_collection.count_documents({}) == 0:
            self.create_genesis_block()
    
    def create_genesis_block(self):
        genesis_block = {
            'index': 0,
            'timestamp': datetime.datetime.now(),
            'content': [],
            'previous_hash': '0',
            'nonce': 0,
            'hash': self.calculate_hash(0, '0', [], 0)
        }
        blocks_collection.insert_one(genesis_block)
    
    def calculate_hash(self, index, previous_hash, content, nonce):
        value = f"{index}{previous_hash}{json.dumps(content, cls=JSONEncoder)}{nonce}".encode()
        return hashlib.sha256(value).hexdigest()
    
    def proof_of_work(self, block):
        block['nonce'] = 0
        computed_hash = self.calculate_hash(block['index'], block['previous_hash'], block['content'], block['nonce'])
        
        while not computed_hash.startswith('0' * self.difficulty):
            block['nonce'] += 1
            computed_hash = self.calculate_hash(block['index'], block['previous_hash'], block['content'], block['nonce'])
        
        return computed_hash
    
    def add_content(self, content_data):
        self.current_content.append(content_data)
        
        # If we have 5 pieces of content, create a new block
        if len(self.current_content) >= 5:
            self.create_block()
    
    def create_block(self):
        if not self.current_content:
            return False
        
        last_block = list(blocks_collection.find().sort('index', -1).limit(1))[0]
        
        new_block = {
            'index': last_block['index'] + 1,
            'timestamp': datetime.datetime.now(),
            'content': self.current_content,
            'previous_hash': last_block['hash'],
            'nonce': 0
        }
        
        new_block['hash'] = self.proof_of_work(new_block)
        blocks_collection.insert_one(new_block)
        self.current_content = []
        return True
    
    def is_chain_valid(self):
        blocks = list(blocks_collection.find().sort('index', 1))
        
        for i in range(1, len(blocks)):
            current_block = blocks[i]
            previous_block = blocks[i-1]
            
            # Check if current block hash is valid
            if current_block['hash'] != self.calculate_hash(
                current_block['index'], 
                current_block['previous_hash'], 
                current_block['content'], 
                current_block['nonce']
            ):
                return False
            
            # Check if previous hash matches
            if current_block['previous_hash'] != previous_block['hash']:
                return False
        
        return True

# Initialize blockchain
blockchain = ContentChain()

# API Routes
@app.route('/api/check_auth')
def check_auth():
    if 'user_id' in session:
        user = users_collection.find_one({'_id': ObjectId(session['user_id'])})
        if user:
            return jsonify({
                'logged_in': True,
                'username': user['username'],
                'user_id': str(user['_id'])
            })
    return jsonify({'logged_in': False})

@app.route('/api/login', methods=['POST'])
def api_login():
    email = request.form.get('email')
    password = request.form.get('password')
    
    user = users_collection.find_one({'email': email})
    if user and bcrypt.check_password_hash(user['password'], password):
        session['user_id'] = str(user['_id'])
        session['user_role'] = user.get('role', 'publisher')
        session['username'] = user['username']
        return jsonify({'success': True, 'message': 'Login successful'})
    
    return jsonify({'success': False, 'message': 'Invalid credentials'})

@app.route('/api/register', methods=['POST'])
def api_register():
    username = request.form.get('username')
    email = request.form.get('email')
    password = request.form.get('password')
    
    # Check if user already exists
    if users_collection.find_one({'email': email}):
        return jsonify({'success': False, 'message': 'User already exists'})
    
    # Hash password
    hashed_password = bcrypt.generate_password_hash(password).decode('utf-8')
    
    # Create user
    user_id = users_collection.insert_one({
        'username': username,
        'email': email,
        'password': hashed_password,
        'role': 'publisher',  # Default role for all users
        'created_at': datetime.datetime.now()
    }).inserted_id
    
    # Initialize reputation
    reputation_collection.insert_one({
        'user_id': user_id,
        'score': 50,
        'verified_content': 0,
        'flagged_content': 0
    })
    
    session['user_id'] = str(user_id)
    session['user_role'] = 'publisher'
    session['username'] = username
    
    return jsonify({'success': True, 'message': 'Registration successful'})

@app.route('/api/logout')
def api_logout():
    session.clear()
    return jsonify({'success': True, 'message': 'Logged out successfully'})

@app.route('/api/dashboard')
@login_required
def api_dashboard():
    # Get user reputation
    reputation = reputation_collection.find_one({'user_id': ObjectId(session['user_id'])})
    
    # Get recent content by this user
    recent_content = list(content_collection.find({'author_id': ObjectId(session['user_id'])}).sort('timestamp', -1).limit(10))
    
    # Get blockchain info
    total_blocks = blocks_collection.count_documents({})
    total_content = content_collection.count_documents({'author_id': ObjectId(session['user_id'])})
    
    return jsonify({
        'reputation': reputation['score'] if reputation else 50,
        'recent_content': recent_content,
        'total_blocks': total_blocks,
        'total_content': total_content
    })

@app.route('/api/submit_content', methods=['POST'])
@login_required
def api_submit_content():
    title = request.form.get('title')
    content_text = request.form.get('content')
    content_type = request.form.get('type', 'article')
    
    # Generate content hash
    content_hash = hashlib.sha256(content_text.encode()).hexdigest()
    
    # Check if content already exists
    if content_collection.find_one({'hash': content_hash}):
        return jsonify({'success': False, 'error': 'Content already exists'})
    
    # AI fact-checking simulation (placeholder)
    ai_verification = {
        'fact_check_score': 85,  # Simulated score
        'is_plagiarized': False,  # Simulated check
        'deepfake_detected': False if content_type == 'article' else None,
        'verification_status': 'pending'
    }
    
    # Create content document
    content_id = content_collection.insert_one({
        'title': title,
        'content': content_text,
        'type': content_type,
        'hash': content_hash,
        'author_id': ObjectId(session['user_id']),
        'author_name': session['username'],
        'timestamp': datetime.datetime.now(),
        'ai_verification': ai_verification,
        'community_votes': {'trust': 0, 'no_trust': 0},
        'verification_status': ai_verification['verification_status']
    }).inserted_id
    
    # Add to blockchain
    content_data = {
        'content_id': str(content_id),
        'hash': content_hash,
        'timestamp': datetime.datetime.now(),
        'title': title,
        'author': session['username']
    }
    blockchain.add_content(content_data)
    
    # Update user reputation
    reputation_collection.update_one(
        {'user_id': ObjectId(session['user_id'])},
        {'$inc': {'score': 5, 'verified_content': 1}}
    )
    
    return jsonify({'success': True, 'content_id': str(content_id), 'hash': content_hash})

@app.route('/api/verify_content', methods=['POST'])
def api_verify_content():
    content_hash = request.form.get('hash')
    content_url = request.form.get('url')
    
    # For demo purposes, we'll just check by hash
    if content_hash:
        content = content_collection.find_one({'hash': content_hash})
    else:
        # In a real implementation, you would extract content from URL
        content = None
    
    if content:
        return jsonify({
            'exists': True,
            'title': content['title'],
            'author': content['author_name'],
            'timestamp': content['timestamp'].strftime('%Y-%m-%d %H:%M:%S'),
            'verification_status': content['verification_status'],
            'ai_verification': content['ai_verification'],
            'community_votes': content['community_votes']
        })
    else:
        return jsonify({'exists': False})

@app.route('/api/vote_content', methods=['POST'])
@login_required
def api_vote_content():
    content_id = request.form.get('content_id')
    vote_type = request.form.get('vote_type')  # 'trust' or 'no_trust'
    
    content = content_collection.find_one({'_id': ObjectId(content_id)})
    if not content:
        return jsonify({'success': False, 'error': 'Content not found'}), 404
    
    # Update votes
    update_field = f'community_votes.{vote_type}'
    content_collection.update_one(
        {'_id': ObjectId(content_id)},
        {'$inc': {update_field: 1}}
    )
    
    # Update author reputation (simplified)
    if vote_type == 'trust':
        reputation_collection.update_one(
            {'user_id': content['author_id']},
            {'$inc': {'score': 1}}
        )
    else:
        reputation_collection.update_one(
            {'user_id': content['author_id']},
            {'$inc': {'score': -2}}
        )
    
    return jsonify({'success': True})

@app.route('/api/blockchain_explorer')
def api_blockchain_explorer():
    blocks = list(blocks_collection.find().sort('index', 1))
    return jsonify({'blocks': blocks})

@app.route('/api/search_content')
def api_search_content():
    query = request.args.get('q')
    
    if not query:
        return jsonify({'success': False, 'error': 'No search query provided'}), 400
    
    # Search by hash or title
    content = list(content_collection.find({
        '$or': [
            {'hash': {'$regex': query, '$options': 'i'}},
            {'title': {'$regex': query, '$options': 'i'}}
        ]
    }).limit(20))
    
    return jsonify({'success': True, 'results': content})

# HTML Routes
@app.route('/')
def index():
    return render_template('index.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        email = request.form.get('email')
        password = request.form.get('password')
        
        user = users_collection.find_one({'email': email})
        if user and bcrypt.check_password_hash(user['password'], password):
            session['user_id'] = str(user['_id'])
            session['user_role'] = user.get('role', 'publisher')
            session['username'] = user['username']
            return redirect(url_for('index'))
        
        return render_template('index.html', login_error="Invalid credentials")
    
    return render_template('index.html')

@app.route('/register', methods=['POST'])
def register():
    username = request.form.get('username')
    email = request.form.get('email')
    password = request.form.get('password')
    
    # Check if user already exists
    if users_collection.find_one({'email': email}):
        return render_template('index.html', register_error="User already exists")
    
    # Hash password
    hashed_password = bcrypt.generate_password_hash(password).decode('utf-8')
    
    # Create user
    user_id = users_collection.insert_one({
        'username': username,
        'email': email,
        'password': hashed_password,
        'role': 'publisher',
        'created_at': datetime.datetime.now()
    }).inserted_id
    
    # Initialize reputation
    reputation_collection.insert_one({
        'user_id': user_id,
        'score': 50,
        'verified_content': 0,
        'flagged_content': 0
    })
    
    session['user_id'] = str(user_id)
    session['user_role'] = 'publisher'
    session['username'] = username
    
    return redirect(url_for('index'))

@app.route('/dashboard')
@login_required
def dashboard():
    return render_template('index.html')

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('index'))

if __name__ == '__main__':
    app.run(debug=True)