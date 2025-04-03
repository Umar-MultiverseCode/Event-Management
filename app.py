from flask import Flask, render_template, request, redirect, url_for, flash, jsonify, send_from_directory, session
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
import time
from sqlalchemy import func

app = Flask(__name__)
app.config['SECRET_KEY'] = 'your-secret-key-here'
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

# User Event Like model
class UserEventLike(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    event_id = db.Column(db.Integer, db.ForeignKey('event.id'), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    # Add unique constraint to prevent duplicate likes
    __table_args__ = (db.UniqueConstraint('user_id', 'event_id', name='unique_user_event_like'),)

    def __repr__(self):
        return f'<UserEventLike {self.user_id} -> {self.event_id}>'

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
    
    # Add likes relationship
    liked_events = db.relationship('UserEventLike', backref='user', lazy=True)

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
    
    # Add likes relationship
    user_likes = db.relationship('UserEventLike', backref='event', lazy=True)

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
    # Get user's created events count
    created_events = Event.query.filter_by(organizer_id=current_user.id).count()
    
    # Get user's joined events count
    joined_events = UserEventLike.query.filter_by(user_id=current_user.id).count()
    
    # Get user's points from UserPoints model
    points = UserPoints.query.filter_by(user_id=current_user.id).first()
    total_points = points.points if points else 0
    
    # Get user's badges count from UserAchievement model
    badges = UserAchievement.query.filter_by(user_id=current_user.id).count()
    
    # Get upcoming events
    upcoming_events = Event.query.filter(
        Event.date >= datetime.now(),
        Event.organizer_id == current_user.id
    ).order_by(Event.date.asc()).limit(6).all()
    
    # Prepare stats dictionary
    stats = {
        'created_events': created_events,
        'joined_events': joined_events,
        'points': total_points,
        'badges': badges
    }
    
    return render_template('dashboard.html', stats=stats, upcoming_events=upcoming_events)

@app.route('/upcoming_events')
@login_required
def upcoming_events():
    # Get current date
    current_date = datetime.utcnow()
    
    # Query upcoming events
    events = Event.query.filter(
        Event.date > current_date
    ).order_by(Event.date.asc()).all()
    
    # Debug print
    print(f"Found {len(events)} upcoming events")
    for event in events:
        print(f"Event: {event.title}, Date: {event.date}")
    
    return render_template('upcoming_events.html', events=events)

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
    
    # Get comments with formatted dates
    comments = EventComment.query.filter_by(event_id=event_id).order_by(EventComment.created_at.desc()).all()
    formatted_comments = []
    for comment in comments:
        user = User.query.get(comment.user_id)
        formatted_comments.append({
            'id': comment.id,
            'content': comment.content,
            'username': user.username,
            'created_at': comment.created_at.strftime('%B %d, %Y at %I:%M %p')
        })
    
    # Check if current user has liked the event
    has_liked = UserEventLike.query.filter_by(
        user_id=current_user.id,
        event_id=event_id
    ).first() is not None
    
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
                         is_registered=is_registered,
                         comments=formatted_comments,
                         has_liked=has_liked)

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
    """Initialize the database with sample data"""
    with app.app_context():
        # Drop all existing tables
        db.drop_all()
        # Create all tables
        db.create_all()
        
        # Create a test user
        test_user = User(
            username='test',
            email='test@example.com'
        )
        test_user.set_password('test123')
        db.session.add(test_user)
        db.session.commit()
        
        # Create sample events
        current_date = datetime.utcnow()
        
        # Event categories and their details
        categories = {
            'Technology': {
                'venues': ['Tech Hub', 'Innovation Center', 'Digital Campus', 'Tech Park'],
                'organizers': ['Tech Events Inc.', 'Digital Solutions', 'Innovation Labs', 'Tech World'],
                'tags': ['technology', 'AI', 'blockchain', 'cloud', 'cybersecurity'],
                'image_url': 'https://images.unsplash.com/photo-1518770660439-4636190af475?ixlib=rb-1.2.1&auto=format&fit=crop&w=1350&q=80'
            },
            'Music': {
                'venues': ['Concert Hall', 'Music Arena', 'Live House', 'Festival Grounds'],
                'organizers': ['Music Events Co.', 'Sound Productions', 'Live Music Network', 'Festival Organizers'],
                'tags': ['music', 'concert', 'festival', 'live', 'performance'],
                'image_url': 'https://images.unsplash.com/photo-1470229722913-7c0e2dbbafd3?ixlib=rb-1.2.1&auto=format&fit=crop&w=1350&q=80'
            },
            'Business': {
                'venues': ['Business Center', 'Conference Hall', 'Corporate Hub', 'Trade Center'],
                'organizers': ['Business Network', 'Corporate Events', 'Trade Association', 'Business Solutions'],
                'tags': ['business', 'networking', 'conference', 'workshop', 'seminar'],
                'image_url': 'https://images.unsplash.com/photo-1542744173-8e7e53415bb0?ixlib=rb-1.2.1&auto=format&fit=crop&w=1350&q=80'
            },
            'Sports': {
                'venues': ['Sports Arena', 'Stadium', 'Sports Complex', 'Fitness Center'],
                'organizers': ['Sports Events', 'Fitness Network', 'Athletic Association', 'Sports Management'],
                'tags': ['sports', 'fitness', 'competition', 'tournament', 'athletics'],
                'image_url': 'https://images.unsplash.com/photo-1517649763962-0c623066013b?ixlib=rb-1.2.1&auto=format&fit=crop&w=1350&q=80'
            },
            'Art': {
                'venues': ['Art Gallery', 'Museum', 'Cultural Center', 'Exhibition Hall'],
                'organizers': ['Art Society', 'Cultural Events', 'Creative Network', 'Art Foundation'],
                'tags': ['art', 'exhibition', 'culture', 'creative', 'gallery'],
                'image_url': 'https://images.unsplash.com/photo-1500462918059-b1a0cb512f1d?ixlib=rb-1.2.1&auto=format&fit=crop&w=1350&q=80'
            },
            'Food': {
                'venues': ['Food Court', 'Culinary Center', 'Restaurant', 'Food Festival Grounds'],
                'organizers': ['Food Events', 'Culinary Network', 'Food Festival', 'Gourmet Society'],
                'tags': ['food', 'culinary', 'cooking', 'gourmet', 'festival'],
                'image_url': 'https://images.unsplash.com/photo-1504674900247-0877df9cc836?ixlib=rb-1.2.1&auto=format&fit=crop&w=1350&q=80'
            }
        }
        
        # Create 30 events
        events = []
        for i in range(30):
            # Randomly select category
            category = random.choice(list(categories.keys()))
            cat_details = categories[category]
            
            # Generate random dates between 1 and 90 days from now
            days_from_now = random.randint(1, 90)
            event_date = current_date + timedelta(days=days_from_now)
            
            # Create event
            event = Event(
                title=f"{category} Event {i+1}",
                description=f"Join us for an exciting {category.lower()} event featuring industry experts and networking opportunities. This event will showcase the latest trends and developments in the field.",
                date=event_date,
                venue=random.choice(cat_details['venues']),
                category=category,
                capacity=random.randint(50, 1000),
                price=random.uniform(0, 200),
                organizer=random.choice(cat_details['organizers']),
                contact_email=f"info@{random.choice(cat_details['organizers']).lower().replace(' ', '')}.com",
                contact_phone=f"+1 ({random.randint(100, 999)}) {random.randint(100, 999)}-{random.randint(1000, 9999)}",
                requirements="No specific requirements",
                schedule=f"9:00 AM - Registration\n10:00 AM - Main Event\n12:00 PM - Lunch\n2:00 PM - Activities\n5:00 PM - Networking",
                speakers=f"Expert Speaker {random.randint(1, 5)}",
                sponsors=f"Sponsor {random.randint(1, 3)}",
                tags=','.join(random.sample(cat_details['tags'], 3)),
                registration_deadline=event_date - timedelta(days=random.randint(1, 7)),
                is_featured=random.choice([True, False]),
                organizer_id=test_user.id,
                created_by=test_user.id,
                image_url=cat_details['image_url']  # Add image URL based on category
            )
            events.append(event)
        
        # Add all events to the database
        for event in events:
            db.session.add(event)
        
        # Commit all changes
        db.session.commit()
        print("Database initialized with 30 sample events.")

@app.route('/event/<int:event_id>/comment', methods=['POST'])
@login_required
def add_comment(event_id):
    event = Event.query.get_or_404(event_id)
    content = request.form.get('content')
    
    if content:
        comment = EventComment(
            event_id=event_id,
            user_id=current_user.id,
            content=content
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
    user_like = UserEventLike.query.filter_by(
        user_id=current_user.id,
        event_id=event_id
    ).first()
    
    if user_like:
        db.session.delete(user_like)
        event.likes -= 1
        action = 'unliked'
    else:
        user_like = UserEventLike(user_id=current_user.id, event_id=event_id)
        db.session.add(user_like)
        event.likes += 1
        action = 'liked'
    
    db.session.commit()
    return jsonify({
        'likes': event.likes,
        'action': action
    })

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
    try:
        # Get search parameters
        query = request.args.get('q', '').strip()
        selected_category = request.args.get('category', '')
        date_from = request.args.get('date_from', '')
        date_to = request.args.get('date_to', '')
        min_price = request.args.get('min_price', '')
        max_price = request.args.get('max_price', '')

        # Get all unique categories for the filter dropdown
        categories = db.session.query(Event.category).distinct().all()
        categories = [category[0] for category in categories if category[0]]

        # Base query
        events_query = Event.query

        # Apply search query filter
        if query:
            # Convert query to lowercase for case-insensitive search
            query = query.lower()
            
            # Create search conditions for each field
            search_conditions = [
                func.lower(Event.title).like(f'%{query}%'),
                func.lower(Event.description).like(f'%{query}%'),
                func.lower(Event.venue).like(f'%{query}%'),
                func.lower(Event.category).like(f'%{query}%'),
                func.lower(Event.organizer).like(f'%{query}%'),
                func.lower(Event.tags).like(f'%{query}%')
            ]
            
            # Combine conditions with OR to match any field
            events_query = events_query.filter(db.or_(*search_conditions))

        # Apply category filter
        if selected_category:
            events_query = events_query.filter(Event.category == selected_category)

        # Apply date range filter
        if date_from:
            try:
                date_from_obj = datetime.strptime(date_from, '%Y-%m-%d')
                events_query = events_query.filter(Event.date >= date_from_obj)
            except ValueError:
                pass

        if date_to:
            try:
                date_to_obj = datetime.strptime(date_to, '%Y-%m-%d')
                events_query = events_query.filter(Event.date <= date_to_obj)
            except ValueError:
                pass

        # Apply price range filter
        if min_price:
            try:
                min_price_float = float(min_price)
                events_query = events_query.filter(Event.price >= min_price_float)
            except ValueError:
                pass

        if max_price:
            try:
                max_price_float = float(max_price)
                events_query = events_query.filter(Event.price <= max_price_float)
            except ValueError:
                pass

        # Get filtered events
        events = events_query.order_by(Event.date.asc()).all()

        # Check if it's an AJAX request
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            return render_template('search_results.html', events=events)
        else:
            return render_template('search.html',
                                events=events,
                                query=query,
                                selected_category=selected_category,
                                date_from=date_from,
                                date_to=date_to,
                                min_price=min_price,
                                max_price=max_price,
                                categories=categories)
    except Exception as e:
        print(f"Search error: {str(e)}")
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            return render_template('search_results.html', events=[])
        else:
            return render_template('search.html', events=[], categories=[])

@app.route('/achievements')
@login_required
def achievements():
    all_achievements = Achievement.query.all()
    user_achievements = {ua.achievement_id for ua in current_user.achievements}
    return render_template('achievements.html', 
                         achievements=all_achievements,
                         user_achievements=user_achievements)

@app.route('/toggle_night_mode', methods=['POST'])
def toggle_night_mode():
    if 'night_mode' not in session:
        session['night_mode'] = False
    session['night_mode'] = not session['night_mode']
    return jsonify({'success': True, 'night_mode': session['night_mode']})

@app.route('/leaderboard')
def leaderboard():
    # Get top organizers
    top_organizers = User.query.join(Event, User.id == Event.organizer_id)\
        .outerjoin(EventRating, Event.id == EventRating.event_id)\
        .group_by(User.id)\
        .with_entities(
            User.id,
            User.username,
            func.count(Event.id).label('events_created'),
            func.sum(Event.capacity).label('total_participants'),
            func.coalesce(func.avg(EventRating.rating), 0).label('rating')
        )\
        .order_by(func.count(Event.id).desc())\
        .limit(10)\
        .all()

    # Get top participants
    top_participants = User.query.join(UserPoints, User.id == UserPoints.user_id)\
        .group_by(User.id)\
        .with_entities(
            User.id,
            User.username,
            func.count(UserEventLike.id).label('events_joined'),
            func.coalesce(UserPoints.points, 0).label('points')
        )\
        .order_by(func.coalesce(UserPoints.points, 0).desc())\
        .limit(10)\
        .all()

    # Create a dictionary to store badges for each participant
    participant_badges = {}
    for participant in top_participants:
        badges = []
        if participant.events_joined >= 10:
            badges.append({
                'name': 'Event Enthusiast',
                'icon': 'star',
                'description': 'Joined 10 or more events'
            })
        if participant.points >= 100:
            badges.append({
                'name': 'High Scorer',
                'icon': 'trophy',
                'description': 'Earned 100 or more points'
            })
        if participant.events_joined >= 5 and participant.points >= 50:
            badges.append({
                'name': 'Active Member',
                'icon': 'award',
                'description': 'Active participation in events'
            })
        participant_badges[participant.id] = badges

    return render_template('leaderboard.html', 
                         top_organizers=top_organizers,
                         top_participants=top_participants,
                         participant_badges=participant_badges)

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

@app.route('/event/<int:event_id>/edit', methods=['GET', 'POST'])
@login_required
def edit_event(event_id):
    event = Event.query.get_or_404(event_id)
    
    # Check if user is the organizer
    if event.organizer_id != current_user.id:
        flash('You are not authorized to edit this event.', 'error')
        return redirect(url_for('event_details', event_id=event_id))
    
    if request.method == 'POST':
        # Update event details
        event.title = request.form.get('title', event.title)
        event.description = request.form.get('description', event.description)
        event.date = datetime.strptime(request.form.get('date'), '%Y-%m-%dT%H:%M')
        event.venue = request.form.get('venue', event.venue)
        event.category = request.form.get('category', event.category)
        event.capacity = int(request.form.get('capacity', event.capacity))
        event.price = float(request.form.get('price', event.price))
        event.requirements = request.form.get('requirements', event.requirements)
        event.schedule = request.form.get('schedule', event.schedule)
        event.speakers = request.form.get('speakers', event.speakers)
        event.sponsors = request.form.get('sponsors', event.sponsors)
        event.tags = request.form.get('tags', event.tags)
        event.registration_deadline = datetime.strptime(request.form.get('registration_deadline'), '%Y-%m-%dT%H:%M')
        
        # Handle image upload
        if 'image' in request.files:
            file = request.files['image']
            if file and allowed_file(file.filename):
                filename = secure_filename(f"{event.id}_{file.filename}")
                file.save(os.path.join(app.config['UPLOAD_FOLDER'], 'events', filename))
                event.image_path = os.path.join('events', filename)
        
        db.session.commit()
        flash('Event updated successfully!', 'success')
        return redirect(url_for('event_details', event_id=event_id))
    
    return render_template('edit_event.html', event=event)

@app.route('/create_event', methods=['GET', 'POST'])
@login_required
def create_event():
    if request.method == 'POST':
        title = request.form.get('title')
        description = request.form.get('description')
        date = datetime.strptime(request.form.get('date'), '%Y-%m-%dT%H:%M')
        venue = request.form.get('venue')
        category = request.form.get('category')
        capacity = int(request.form.get('capacity'))
        price = float(request.form.get('price'))
        organizer = request.form.get('organizer')
        contact_email = request.form.get('contact_email')
        contact_phone = request.form.get('contact_phone')
        requirements = request.form.get('requirements')
        schedule = request.form.get('schedule')
        speakers = request.form.get('speakers')
        sponsors = request.form.get('sponsors')
        tags = request.form.get('tags')
        registration_deadline = datetime.strptime(request.form.get('registration_deadline'), '%Y-%m-%dT%H:%M')
        
        # Handle image upload
        image_path = None
        if 'image' in request.files:
            file = request.files['image']
            if file and allowed_file(file.filename):
                filename = secure_filename(f"{current_user.id}_{datetime.now().strftime('%Y%m%d%H%M%S')}_{file.filename}")
                file.save(os.path.join(app.config['UPLOAD_FOLDER'], 'events', filename))
                image_path = os.path.join('events', filename)
        
        event = Event(
            title=title,
            description=description,
            date=date,
            venue=venue,
            category=category,
            capacity=capacity,
            price=price,
            organizer=organizer,
            contact_email=contact_email,
            contact_phone=contact_phone,
            requirements=requirements,
            schedule=schedule,
            speakers=speakers,
            sponsors=sponsors,
            tags=tags,
            registration_deadline=registration_deadline,
            image_path=image_path,
            organizer_id=current_user.id,
            created_by=current_user.id
        )
        
        db.session.add(event)
        db.session.commit()
        
        flash('Event created successfully!', 'success')
        return redirect(url_for('event_details', event_id=event.id))
    
    return render_template('create_event.html')

if __name__ == '__main__':
    # Initialize the database
    with app.app_context():
        # Check if database exists
        if not os.path.exists('events.db'):
            print("Creating new database...")
            init_db()
        else:
            print("Database already exists. Skipping initialization.")
    
    # Run the application
    app.run(host='0.0.0.0', port=5000, debug=True) 