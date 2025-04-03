from flask import Flask, render_template, request, redirect, url_for, flash, jsonify, send_from_directory
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime, timedelta
import random
from flask_socketio import SocketIO, emit, join_room, leave_room
import os
from werkzeug.utils import secure_filename
import json
import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity
import pandas as pd
from collections import defaultdict

app = Flask(__name__)
app.config['SECRET_KEY'] = 'your-secret-key'
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///events.db'
app.config['UPLOAD_FOLDER'] = 'static/uploads'
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # 16MB max file size
app.config['ALLOWED_EXTENSIONS'] = {'png', 'jpg', 'jpeg', 'gif'}

# Create upload folders if they don't exist
os.makedirs(os.path.join(app.config['UPLOAD_FOLDER'], 'events'), exist_ok=True)
os.makedirs(os.path.join(app.config['UPLOAD_FOLDER'], 'profile_pictures'), exist_ok=True)

db = SQLAlchemy(app)
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'
login_manager.login_message = 'Please log in to access this page.'
login_manager.login_message_category = 'error'
socketio = SocketIO(app)

# Notification model
class Notification(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    message = db.Column(db.String(500), nullable=False)
    is_read = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    type = db.Column(db.String(50))  # 'event_update', 'message', 'system'
    data = db.Column(db.Text)  # JSON string for additional data

# Chat message model
class ChatMessage(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    event_id = db.Column(db.Integer, db.ForeignKey('event.id'), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    message = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    user = db.relationship('User', backref='chat_messages')

# Event Comment model
class EventComment(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    event_id = db.Column(db.Integer, db.ForeignKey('event.id'), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    content = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    parent_id = db.Column(db.Integer, db.ForeignKey('event_comment.id'))
    user = db.relationship('User', backref='comments')
    event = db.relationship('Event', backref='comments')

# Event Rating model
class EventRating(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    event_id = db.Column(db.Integer, db.ForeignKey('event.id'), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    rating = db.Column(db.Integer, nullable=False)
    review = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    user = db.relationship('User', backref='ratings')
    event = db.relationship('Event', backref='ratings')

# Achievement model
class Achievement(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    description = db.Column(db.Text, nullable=False)
    points = db.Column(db.Integer, nullable=False)
    icon = db.Column(db.String(100))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

# User Achievement model
class UserAchievement(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    achievement_id = db.Column(db.Integer, db.ForeignKey('achievement.id'), nullable=False)
    earned_at = db.Column(db.DateTime, default=datetime.utcnow)
    user = db.relationship('User', backref=db.backref('achievements', lazy=True))
    achievement = db.relationship('Achievement')

# User Points model
class UserPoints(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    points = db.Column(db.Integer, default=0)
    level = db.Column(db.Integer, default=1)
    user = db.relationship('User', backref=db.backref('points', uselist=False))

# Update User model
class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password_hash = db.Column(db.String(128))
    profile_picture = db.Column(db.String(255))
    bio = db.Column(db.Text)
    notifications = db.relationship('Notification', backref='user', lazy=True)
    last_seen = db.Column(db.DateTime, default=datetime.utcnow)
    events = db.relationship('Event', secondary='user_events', backref=db.backref('attendees', lazy='dynamic'))
    preferences = db.Column(db.Text)  # JSON string for user preferences

    def __init__(self, **kwargs):
        super(User, self).__init__(**kwargs)
        # Initialize points when a new user is created
        self.points = UserPoints(user_id=self.id, points=0, level=1)
        db.session.add(self.points)

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

    def get_preferences(self):
        if self.preferences:
            return json.loads(self.preferences)
        return {}

    def set_preferences(self, preferences):
        self.preferences = json.dumps(preferences)

    def add_points(self, points):
        if not hasattr(self, 'points') or not self.points:
            self.points = UserPoints(user_id=self.id, points=0, level=1)
            db.session.add(self.points)
        self.points.points += points
        self.update_level()
        db.session.commit()
    
    def update_level(self):
        if not hasattr(self, 'points') or not self.points:
            self.points = UserPoints(user_id=self.id, points=0, level=1)
            db.session.add(self.points)
        # Simple leveling system: level = floor(sqrt(points / 100))
        new_level = int((self.points.points / 100) ** 0.5) + 1
        if new_level > self.points.level:
            self.points.level = new_level
            # Create level up achievement
            achievement = Achievement.query.filter_by(name=f"Level {new_level}").first()
            if achievement:
                self.earn_achievement(achievement)
    
    def earn_achievement(self, achievement):
        if not UserAchievement.query.filter_by(user_id=self.id, achievement_id=achievement.id).first():
            user_achievement = UserAchievement(user_id=self.id, achievement_id=achievement.id)
            db.session.add(user_achievement)
            self.add_points(achievement.points)
            db.session.commit()
            return True
        return False

    def get_recommendations(self, limit=5):
        """Get personalized event recommendations based on user preferences and behavior"""
        # Get user's past events and preferences
        past_events = [event for event in self.events if event.date < datetime.utcnow()]
        preferences = self.get_preferences()
        
        # Get all upcoming events
        upcoming_events = Event.query.filter(Event.date > datetime.utcnow()).all()
        
        if not upcoming_events:
            return []
            
        # Create feature vectors for events
        event_features = []
        for event in upcoming_events:
            features = {
                'title': event.title,
                'description': event.description,
                'category': event.category,
                'tags': event.tags or '',
                'venue': event.venue
            }
            event_features.append(' '.join(str(v) for v in features.values()))
            
        # Create TF-IDF vectors
        vectorizer = TfidfVectorizer()
        tfidf_matrix = vectorizer.fit_transform(event_features)
        
        # Calculate similarity scores
        similarity_scores = []
        for event in upcoming_events:
            score = 0
            
            # Category preference
            if preferences.get('preferred_categories') and event.category in preferences['preferred_categories']:
                score += 2
                
            # Past event similarity
            for past_event in past_events:
                if event.category == past_event.category:
                    score += 1
                if event.venue == past_event.venue:
                    score += 0.5
                    
            # Time preference
            if preferences.get('preferred_time') and event.date.hour in preferences['preferred_time']:
                score += 1
                
            # Price preference
            if preferences.get('max_price') and event.price <= preferences['max_price']:
                score += 1
                
            similarity_scores.append(score)
            
        # Sort events by similarity score
        recommended_events = sorted(zip(upcoming_events, similarity_scores), 
                                 key=lambda x: x[1], reverse=True)
        
        return [event for event, _ in recommended_events[:limit]]

# Update Event model
class Event(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(100), nullable=False)
    description = db.Column(db.Text, nullable=False)
    date = db.Column(db.DateTime, nullable=False)
    venue = db.Column(db.String(200), nullable=False)
    category = db.Column(db.String(50), nullable=False)
    capacity = db.Column(db.Integer, nullable=False)
    price = db.Column(db.Float, nullable=False)
    organizer = db.Column(db.String(100), nullable=False)
    contact_email = db.Column(db.String(120), nullable=False)
    contact_phone = db.Column(db.String(20), nullable=False)
    requirements = db.Column(db.Text)
    schedule = db.Column(db.Text)
    speakers = db.Column(db.Text)
    sponsors = db.Column(db.Text)
    image_url = db.Column(db.String(255))  # For external images
    image_path = db.Column(db.String(255))  # For uploaded images
    organizer_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    created_by = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    status = db.Column(db.String(20), default='upcoming')  # upcoming, ongoing, completed, cancelled
    is_featured = db.Column(db.Boolean, default=False)
    registration_deadline = db.Column(db.DateTime)
    tags = db.Column(db.String(255))  # Comma-separated tags
    additional_images = db.Column(db.Text)  # JSON string for multiple images
    likes = db.Column(db.Integer, default=0)
    shares = db.Column(db.Integer, default=0)
    views = db.Column(db.Integer, default=0)
    revenue = db.Column(db.Float, default=0.0)
    chat_messages = db.relationship('ChatMessage', backref='event', lazy=True)

    def get_image_url(self):
        if self.image_path:
            return url_for('uploaded_file', filename=f'events/{os.path.basename(self.image_path)}')
        return self.image_url or url_for('static', filename='images/default-event.jpg')

    def get_additional_images(self):
        if self.additional_images:
            return json.loads(self.additional_images)
        return []

    def increment_likes(self):
        self.likes += 1
        db.session.commit()

    def increment_shares(self):
        self.shares += 1
        db.session.commit()

    def increment_views(self):
        self.views += 1
        db.session.commit()

    def get_similar_events(self, limit=3):
        """Get similar events based on content similarity"""
        # Get all upcoming events
        upcoming_events = Event.query.filter(
            Event.date > datetime.utcnow(),
            Event.id != self.id
        ).all()
        
        if not upcoming_events:
            return []
            
        # Create feature vectors
        events = [self] + upcoming_events
        event_features = []
        for event in events:
            features = {
                'title': event.title,
                'description': event.description,
                'category': event.category,
                'tags': event.tags or '',
                'venue': event.venue
            }
            event_features.append(' '.join(str(v) for v in features.values()))
            
        # Create TF-IDF vectors
        vectorizer = TfidfVectorizer()
        tfidf_matrix = vectorizer.fit_transform(event_features)
        
        # Calculate similarity scores
        similarity_scores = cosine_similarity(tfidf_matrix[0:1], tfidf_matrix[1:])[0]
        
        # Sort events by similarity score
        similar_events = sorted(zip(upcoming_events, similarity_scores), 
                              key=lambda x: x[1], reverse=True)
        
        return [event for event, _ in similar_events[:limit]]

# Association table for user-events relationship
user_events = db.Table('user_events',
    db.Column('user_id', db.Integer, db.ForeignKey('user.id'), primary_key=True),
    db.Column('event_id', db.Integer, db.ForeignKey('event.id'), primary_key=True)
)

# WebSocket event handlers
@socketio.on('join')
def on_join(data):
    room = data['room']
    join_room(room)
    emit('status', {'msg': f'{current_user.username} has joined the room.'}, room=room)

@socketio.on('leave')
def on_leave(data):
    room = data['room']
    leave_room(room)
    emit('status', {'msg': f'{current_user.username} has left the room.'}, room=room)

@socketio.on('chat_message')
def handle_chat_message(data):
    room = data['room']
    message = data['message']
    
    # Save message to database
    chat_message = ChatMessage(
        event_id=room,
        user_id=current_user.id,
        message=message
    )
    db.session.add(chat_message)
    db.session.commit()
    
    # Emit message to room
    emit('chat_message', {
        'user': current_user.username,
        'message': message,
        'timestamp': datetime.utcnow().strftime('%H:%M')
    }, room=room)

@socketio.on('typing')
def handle_typing(data):
    room = data['room']
    emit('typing', {
        'user': current_user.username
    }, room=room, include_self=False)

# Notification routes
@app.route('/notifications')
@login_required
def notifications():
    user_notifications = Notification.query.filter_by(user_id=current_user.id).order_by(Notification.created_at.desc()).all()
    return render_template('notifications.html', notifications=user_notifications)

@app.route('/notifications/count')
@login_required
def notification_count():
    count = Notification.query.filter_by(user_id=current_user.id, is_read=False).count()
    return jsonify({'count': count})

@app.route('/notifications/read/<int:notification_id>', methods=['POST'])
@login_required
def mark_notification_read(notification_id):
    notification = Notification.query.get_or_404(notification_id)
    if notification.user_id != current_user.id:
        return jsonify({'error': 'Unauthorized'}), 403
    
    notification.is_read = True
    db.session.commit()
    return jsonify({'success': True})

@app.route('/notifications/read_all', methods=['POST'])
@login_required
def mark_all_notifications_read():
    Notification.query.filter_by(user_id=current_user.id, is_read=False).update({'is_read': True})
    db.session.commit()
    return jsonify({'success': True})

# Update existing routes to include notifications
@app.route('/join_event/<int:event_id>', methods=['POST'])
@login_required
def join_event(event_id):
    event = Event.query.get_or_404(event_id)
    if event not in current_user.events:
        current_user.events.append(event)
        
        # Create notification for event organizer
        notification = Notification(
            user_id=event.organizer_id,  # Assuming organizer_id is added to Event model
            message=f'{current_user.username} has joined your event: {event.title}',
            type='event_update',
            data=json.dumps({'event_id': event.id})
        )
        db.session.add(notification)
        
        db.session.commit()
        flash('You have successfully joined the event!', 'success')
    return redirect(url_for('event_details', event_id=event_id))

# Add new route for event chat
@app.route('/event/<int:event_id>/chat')
@login_required
def event_chat(event_id):
    event = Event.query.get_or_404(event_id)
    if event not in current_user.events:
        flash('You must join the event to access the chat.', 'error')
        return redirect(url_for('event_details', event_id=event_id))
    
    messages = ChatMessage.query.filter_by(event_id=event_id).order_by(ChatMessage.created_at.asc()).all()
    return render_template('event_chat.html', event=event, messages=messages)

@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

# Routes
@app.route('/')
def index():
    return redirect(url_for('login'))

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        user = User.query.filter_by(username=username).first()
        
        if user and user.check_password(password):
            login_user(user)
            return redirect(url_for('dashboard'))
        flash('Invalid username or password')
    return render_template('login.html')

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        email = request.form.get('email')
        
        if User.query.filter_by(username=username).first():
            flash('Username already exists')
            return redirect(url_for('register'))
        
        if User.query.filter_by(email=email).first():
            flash('Email already registered')
            return redirect(url_for('register'))
        
        user = User(username=username, email=email)
        user.set_password(password)
        db.session.add(user)
        db.session.commit()
        
        return redirect(url_for('login'))
    return render_template('register.html')

@app.route('/dashboard')
@login_required
def dashboard():
    return render_template('dashboard.html')

@app.route('/upcoming_events')
@login_required
def upcoming_events():
    upcoming_events = Event.query.filter(Event.date > datetime.now()).all()
    return render_template('upcoming_events.html', events=upcoming_events)

@app.route('/joined_events')
@login_required
def joined_events():
    joined_events = current_user.events
    return render_template('joined_events.html', events=joined_events)

@app.route('/event/<int:event_id>')
@login_required
def event_details(event_id):
    event = Event.query.get_or_404(event_id)
    is_organizer = event.organizer_id == current_user.id
    is_registered = event in current_user.events
    
    # Check for achievements
    if is_registered:
        first_event = Achievement.query.filter_by(name="First Event").first()
        if first_event:
            current_user.earn_achievement(first_event)
        
        if len(current_user.events) >= 5:
            event_enthusiast = Achievement.query.filter_by(name="Event Enthusiast").first()
            if event_enthusiast:
                current_user.earn_achievement(event_enthusiast)
    
    return render_template('event_details.html', 
                         event=event,
                         is_organizer=is_organizer,
                         is_registered=is_registered)

@app.route('/exit_event/<int:event_id>', methods=['POST'])
@login_required
def exit_event(event_id):
    event = Event.query.get_or_404(event_id)
    if event in current_user.events:
        current_user.events.remove(event)
        db.session.commit()
        flash('Successfully exited the event!')
    return redirect(url_for('joined_events'))

@app.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('login'))

@app.route('/profile')
@login_required
def profile():
    # Get user's events
    user_events = current_user.events  # This is already a list, no need for .all()
    upcoming_events = [event for event in user_events if event.date > datetime.utcnow()]
    past_events = [event for event in user_events if event.date <= datetime.utcnow()]
    
    # Get user's preferences
    preferences = current_user.get_preferences()
    
    return render_template('profile.html', 
        user=current_user,
        upcoming_events=upcoming_events,
        past_events=past_events,
        preferences=preferences,
        now=datetime.utcnow()
    )

@app.route('/profile/edit', methods=['GET', 'POST'])
@login_required
def edit_profile():
    if request.method == 'POST':
        current_user.email = request.form.get('email', current_user.email)
        current_user.bio = request.form.get('bio', current_user.bio)
        
        # Handle profile picture upload
        if 'profile_picture' in request.files:
            file = request.files['profile_picture']
            if file and allowed_file(file.filename):
                filename = secure_filename(f"{current_user.id}_{file.filename}")
                file.save(os.path.join(app.config['UPLOAD_FOLDER'], filename))
                current_user.profile_picture = filename
        
        # Update preferences
        preferences = current_user.get_preferences()
        preferences['notifications'] = request.form.get('notifications') == 'on'
        preferences['email_updates'] = request.form.get('email_updates') == 'on'
        current_user.set_preferences(preferences)
        
        db.session.commit()
        flash('Profile updated successfully!', 'success')
        return redirect(url_for('profile'))
    
    return render_template('edit_profile.html', 
        user=current_user,
        preferences=current_user.get_preferences()
    )

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in app.config['ALLOWED_EXTENSIONS']

def init_db():
    with app.app_context():
        # Drop all existing tables
        db.drop_all()
        # Create all tables
        db.create_all()
        
        # Create a test user with a unique email
        test_user = User(
            username='test',
            email=f'test_{datetime.now().timestamp()}@example.com'
        )
        test_user.set_password('test123')
        db.session.add(test_user)
        
        # Create sample events with rich content
        events = [
            Event(
                title='Tech Conference 2024',
                description='Join us for the most anticipated tech conference of the year!',
                date=datetime(2024, 6, 15, 9, 0),
                venue='Convention Center, New York',
                category='Technology',
                capacity=1000,
                price=299.99,
                organizer='Tech Innovators Inc.',
                contact_email='info@techconference.com',
                contact_phone='+1 (555) 123-4567',
                requirements='Laptop, Notebook, Business Cards',
                schedule='Day 1: Registration, Keynote, Workshops\nDay 2: Panel Discussions, Networking',
                speakers='John Smith, Sarah Johnson, Michael Brown',
                sponsors='Microsoft, Amazon, Google',
                image_url='https://images.unsplash.com/photo-1505373877841-8d25f7d46678',
                organizer_id=1,
                created_by=1
            ),
            Event(
                title='Music Festival 2024',
                description='Experience three days of incredible music across multiple stages! From rock to electronic, jazz to hip-hop, this festival has something for every music lover. Join thousands of music enthusiasts for an unforgettable weekend of performances, food, and fun.',
                date=datetime(2024, 7, 20, 18, 0),
                venue='Central Park, New York',
                category='Music',
                capacity=5000,
                price=199.99,
                organizer='Music Events Co.',
                contact_email='info@musicfestival.com',
                contact_phone='+1 (555) 987-6543',
                requirements='Valid ID, Comfortable Shoes, Sun Protection',
                schedule='''Friday:
- 6:00 PM: Gates Open
- 7:00 PM: Opening Act
- 9:00 PM: Headliner 1
- 11:00 PM: Closing DJ

Saturday:
- 2:00 PM: Gates Open
- 3:00 PM: Local Bands
- 7:00 PM: Main Stage
- 10:00 PM: Fireworks

Sunday:
- 2:00 PM: Gates Open
- 3:00 PM: Acoustic Stage
- 6:00 PM: Final Headliner
- 9:00 PM: Closing Ceremony''',
                speakers='''Main Stage:
- The Rock Band
- Electronic Duo
- Jazz Ensemble
- Hip-Hop Collective''',
                sponsors='''Presented by:
- Spotify
- Apple Music
- SoundCloud

Sponsored by:
- Red Bull
- Coca-Cola
- Samsung''',
                image_url='https://images.unsplash.com/photo-1470229722913-7c0e2dbbafd3',
                organizer_id=1,
                created_by=1
            ),
            Event(
                title='Startup Pitch Competition',
                description='Calling all entrepreneurs! Present your innovative ideas to a panel of industry experts and potential investors. This competition offers cash prizes, mentorship opportunities, and the chance to connect with venture capitalists.',
                date=datetime(2024, 8, 5, 14, 0),
                venue='Innovation Hub, San Francisco',
                category='Business',
                capacity=200,
                price=49.99,
                organizer='Startup Network',
                contact_email='pitch@startupnetwork.com',
                contact_phone='+1 (555) 456-7890',
                requirements='Pitch Deck, Business Plan, Demo (if applicable)',
                schedule='''2:00 PM: Registration
2:30 PM: Opening Remarks
3:00 PM: Pitch Sessions Begin
5:00 PM: Break
5:30 PM: Final Pitches
6:30 PM: Judging
7:00 PM: Awards Ceremony
7:30 PM: Networking Reception''',
                speakers='''Judges:
- Mark Johnson - Partner, Venture Capital
- Lisa Chen - CEO, Tech Accelerator
- David Wilson - Angel Investor
- Sarah Miller - Startup Advisor''',
                sponsors='''Hosted by:
- Y Combinator
- TechStars
- 500 Startups

Supported by:
- Silicon Valley Bank
- Stripe
- AWS Startups''',
                image_url='https://images.unsplash.com/photo-1552664730-d307ca884978',
                organizer_id=1,
                created_by=1
            )
        ]
        
        # Add more random events
        categories = ['Technology', 'Music', 'Business', 'Art', 'Sports', 'Food', 'Education', 'Health']
        organizers = ['Event Pro', 'Global Events', 'City Events', 'Professional Organizers', 'Event Masters']
        for i in range(17):  # Add 17 more events to make it 20 total
            event_date = datetime.now() + timedelta(days=random.randint(1, 365))
            category = random.choice(categories)
            event = Event(
                title=f'{category} Event {event_date.year}',
                description=f'Join us for an amazing {category.lower()} event featuring special guests and exciting activities. This event promises to be an unforgettable experience with expert speakers, interactive sessions, and networking opportunities.',
                date=event_date,
                venue=random.choice([
                    'Convention Center, New York',
                    'Central Park, New York',
                    'Innovation Hub, San Francisco',
                    'Tech Park, Silicon Valley',
                    'Music Hall, Los Angeles',
                    'Business Center, Chicago',
                    'Art Gallery, Miami',
                    'Sports Arena, Boston',
                    'Conference Center, Seattle',
                    'Exhibition Hall, Las Vegas'
                ]),
                category=category,
                capacity=random.randint(100, 1000),
                price=random.uniform(49.99, 299.99),
                organizer=random.choice(organizers),
                contact_email=f'info@{category.lower()}event.com',
                contact_phone=f'+1 (555) {random.randint(100,999)}-{random.randint(1000,9999)}',
                requirements='Valid ID, Registration Confirmation',
                schedule='''Morning:
- 9:00 AM: Registration
- 10:00 AM: Opening Session
- 12:00 PM: Lunch

Afternoon:
- 2:00 PM: Main Activities
- 4:00 PM: Breakout Sessions
- 6:00 PM: Closing Remarks''',
                speakers='''Featured Speakers:
- Industry Expert 1
- Professional Speaker 2
- Special Guest 3''',
                sponsors='''Sponsored by:
- Company A
- Organization B
- Corporation C''',
                image_url=f'https://source.unsplash.com/random/800x600/?{category.lower()}',
                organizer_id=1,
                created_by=1
            )
            events.append(event)
        
        for event in events:
            db.session.add(event)
        
        # Create default achievements
        achievements = [
            Achievement(name="First Event", description="Attend your first event", points=50, icon="first-event"),
            Achievement(name="Event Enthusiast", description="Attend 5 events", points=100, icon="enthusiast"),
            Achievement(name="Social Butterfly", description="Comment on 10 events", points=75, icon="social"),
            Achievement(name="Event Organizer", description="Create your first event", points=150, icon="organizer"),
            Achievement(name="Level 2", description="Reach level 2", points=0, icon="level-2"),
            Achievement(name="Level 5", description="Reach level 5", points=0, icon="level-5"),
            Achievement(name="Level 10", description="Reach level 10", points=0, icon="level-10")
        ]
        
        for achievement in achievements:
            db.session.add(achievement)
        
        try:
            db.session.commit()
        except Exception as e:
            db.session.rollback()
            print(f"Error during database initialization: {e}")
            raise

@app.route('/event/<int:event_id>/comment', methods=['POST'])
@login_required
def add_comment(event_id):
    event = Event.query.get_or_404(event_id)
    content = request.form.get('content')
    parent_id = request.form.get('parent_id')
    
    if content:
        comment = EventComment(
            event_id=event_id,
            user_id=current_user.id,
            content=content,
            parent_id=parent_id if parent_id else None
        )
        db.session.add(comment)
        db.session.commit()
        flash('Comment added successfully!', 'success')
    
    return redirect(url_for('event_details', event_id=event_id))

@app.route('/event/<int:event_id>/rate', methods=['POST'])
@login_required
def rate_event(event_id):
    event = Event.query.get_or_404(event_id)
    rating = request.form.get('rating')
    review = request.form.get('review')
    
    if rating:
        # Check if user has already rated this event
        existing_rating = EventRating.query.filter_by(
            event_id=event_id,
            user_id=current_user.id
        ).first()
        
        if existing_rating:
            existing_rating.rating = rating
            existing_rating.review = review
        else:
            new_rating = EventRating(
                event_id=event_id,
                user_id=current_user.id,
                rating=rating,
                review=review
            )
            db.session.add(new_rating)
        
        db.session.commit()
        flash('Rating submitted successfully!', 'success')
    
    return redirect(url_for('event_details', event_id=event_id))

@app.route('/event/<int:event_id>/like', methods=['POST'])
@login_required
def like_event(event_id):
    event = Event.query.get_or_404(event_id)
    event.increment_likes()
    return jsonify({'likes': event.likes})

@app.route('/event/<int:event_id>/share', methods=['POST'])
@login_required
def share_event(event_id):
    event = Event.query.get_or_404(event_id)
    event.increment_shares()
    return jsonify({'shares': event.shares})

@app.route('/analytics')
@login_required
def analytics():
    # Get user's event statistics
    user_events = Event.query.filter_by(created_by=current_user.id).all()
    
    # Calculate statistics
    total_events = len(user_events)
    total_views = sum(event.views for event in user_events)
    total_likes = sum(event.likes for event in user_events)
    total_revenue = sum(event.revenue for event in user_events)
    
    # Get event categories distribution
    categories = {}
    for event in user_events:
        categories[event.category] = categories.get(event.category, 0) + 1
    
    # Get recent comments
    recent_comments = EventComment.query.join(Event).filter(
        Event.created_by == current_user.id
    ).order_by(EventComment.created_at.desc()).limit(5).all()
    
    return render_template('analytics.html',
        total_events=total_events,
        total_views=total_views,
        total_likes=total_likes,
        total_revenue=total_revenue,
        categories=categories,
        recent_comments=recent_comments
    )

@app.route('/search')
def search_events():
    query = request.args.get('q', '')
    category = request.args.get('category')
    min_price = request.args.get('min_price')
    max_price = request.args.get('max_price')
    date_from = request.args.get('date_from')
    date_to = request.args.get('date_to')
    
    # Start with base query
    events_query = Event.query
    
    # Apply filters
    if query:
        events_query = events_query.filter(
            (Event.title.ilike(f'%{query}%')) |
            (Event.description.ilike(f'%{query}%')) |
            (Event.tags.ilike(f'%{query}%'))
        )
    
    if category:
        events_query = events_query.filter_by(category=category)
    
    if min_price:
        events_query = events_query.filter(Event.price >= float(min_price))
    
    if max_price:
        events_query = events_query.filter(Event.price <= float(max_price))
    
    if date_from:
        events_query = events_query.filter(Event.date >= datetime.strptime(date_from, '%Y-%m-%d'))
    
    if date_to:
        events_query = events_query.filter(Event.date <= datetime.strptime(date_to, '%Y-%m-%d'))
    
    # Get unique categories for filter dropdown
    categories = db.session.query(Event.category.distinct()).all()
    
    events = events_query.all()
    return render_template('search.html',
        events=events,
        categories=[c[0] for c in categories],
        query=query,
        selected_category=category,
        min_price=min_price,
        max_price=max_price,
        date_from=date_from,
        date_to=date_to
    )

@app.route('/achievements')
@login_required
def achievements():
    all_achievements = Achievement.query.all()
    user_achievements = {ua.achievement_id for ua in current_user.achievements}
    return render_template('achievements.html', 
                         achievements=all_achievements,
                         user_achievements=user_achievements)

@app.route('/leaderboard')
def leaderboard():
    top_users = User.query.join(UserPoints).order_by(UserPoints.points.desc()).limit(10).all()
    return render_template('leaderboard.html', top_users=top_users)

# Add route to serve uploaded files
@app.route('/uploads/<path:filename>')
def uploaded_file(filename):
    return send_from_directory(app.config['UPLOAD_FOLDER'], filename)

@app.route('/recommendations')
@login_required
def recommendations():
    """Get personalized event recommendations"""
    recommended_events = current_user.get_recommendations()
    return render_template('recommendations.html', 
                         recommended_events=recommended_events)

@app.route('/event/<int:event_id>/similar')
def similar_events(event_id):
    """Get similar events for a specific event"""
    event = Event.query.get_or_404(event_id)
    similar_events = event.get_similar_events()
    return render_template('similar_events.html', 
                         event=event,
                         similar_events=similar_events)

if __name__ == '__main__':
    init_db()
    socketio.run(app, debug=True) 