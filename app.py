from flask import Flask, render_template, request, redirect, url_for, flash, send_from_directory
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime, timedelta
import random
import os
from werkzeug.utils import secure_filename

app = Flask(__name__)
app.config['SECRET_KEY'] = 'your-secret-key-here'
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///events.db'
app.config['UPLOAD_FOLDER'] = 'static/uploads'
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # 16MB max file size
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif'}

# Create upload folder if it doesn't exist
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

db = SQLAlchemy(app)
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'

# Database Models
class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password_hash = db.Column(db.String(120), nullable=False)
    joined_events = db.relationship('Event', secondary='user_events', backref='participants')

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

class Event(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(100), nullable=False)
    description = db.Column(db.Text, nullable=False)
    date = db.Column(db.DateTime, nullable=False)
    venue = db.Column(db.String(200), nullable=False)
    created_by = db.Column(db.Integer, db.ForeignKey('user.id'))
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
    image_url = db.Column(db.String(200))

user_events = db.Table('user_events',
    db.Column('user_id', db.Integer, db.ForeignKey('user.id'), primary_key=True),
    db.Column('event_id', db.Integer, db.ForeignKey('event.id'), primary_key=True)
)

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
        
        if User.query.filter_by(username=username).first():
            flash('Username already exists')
            return redirect(url_for('register'))
        
        user = User(username=username)
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
    joined_events = current_user.joined_events
    return render_template('joined_events.html', events=joined_events)

@app.route('/event/<int:event_id>')
@login_required
def event_details(event_id):
    event = Event.query.get_or_404(event_id)
    is_joined = event in current_user.joined_events
    return render_template('event_details.html', event=event, is_joined=is_joined)

@app.route('/join_event/<int:event_id>', methods=['POST'])
@login_required
def join_event(event_id):
    event = Event.query.get_or_404(event_id)
    if event not in current_user.joined_events:
        current_user.joined_events.append(event)
        db.session.commit()
        flash('Successfully joined the event!')
    return redirect(url_for('event_details', event_id=event_id))

@app.route('/exit_event/<int:event_id>', methods=['POST'])
@login_required
def exit_event(event_id):
    event = Event.query.get_or_404(event_id)
    if event in current_user.joined_events:
        current_user.joined_events.remove(event)
        db.session.commit()
        flash('Successfully exited the event!')
    return redirect(url_for('joined_events'))

@app.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('login'))

@app.route('/delete_event/<int:event_id>', methods=['POST'])
@login_required
def delete_event(event_id):
    event = Event.query.get_or_404(event_id)
    
    # Check if the current user is the creator of the event
    if event.created_by != current_user.id:
        flash('You can only delete events that you created.')
        return redirect(url_for('event_details', event_id=event_id))
    
    # Delete the event's image file if it exists in the uploads folder
    if event.image_url and event.image_url.startswith('/uploads/'):
        filename = event.image_url.split('/')[-1]
        file_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
        if os.path.exists(file_path):
            os.remove(file_path)
    
    # Delete the event from the database
    db.session.delete(event)
    db.session.commit()
    
    flash('Event has been successfully deleted.')
    return redirect(url_for('dashboard'))

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

@app.route('/create_event', methods=['GET', 'POST'])
@login_required
def create_event():
    if request.method == 'POST':
        # Get form data
        title = request.form['title']
        description = request.form['description']
        date = datetime.strptime(request.form['date'], '%Y-%m-%dT%H:%M')
        venue = request.form['venue']
        category = request.form['category']
        capacity = int(request.form['capacity'])
        price = float(request.form['price'])
        organizer = request.form['organizer']
        contact_email = request.form['contact_email']
        contact_phone = request.form['contact_phone']
        requirements = request.form.get('requirements', '')
        schedule = request.form.get('schedule', '')
        speakers = request.form.get('speakers', '')
        sponsors = request.form.get('sponsors', '')
        
        # Handle image upload
        image_url = request.form.get('image_url', '')
        if 'image_file' in request.files:
            file = request.files['image_file']
            if file and file.filename and allowed_file(file.filename):
                filename = secure_filename(file.filename)
                # Add timestamp to filename to prevent duplicates
                timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
                filename = f"{timestamp}_{filename}"
                file.save(os.path.join(app.config['UPLOAD_FOLDER'], filename))
                image_url = url_for('uploaded_file', filename=filename)

        # Create new event
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
            image_url=image_url,
            created_by=current_user.id
        )

        # Add to database
        db.session.add(event)
        db.session.commit()

        flash('Event created successfully!')
        return redirect(url_for('event_details', event_id=event.id))

    return render_template('create_event.html')

@app.route('/uploads/<filename>')
def uploaded_file(filename):
    return send_from_directory(app.config['UPLOAD_FOLDER'], filename)

def init_db():
    with app.app_context():
        # Create all tables if they don't exist
        db.create_all()
        
        # Check if we need to add sample data
        if not User.query.first():
            # Create a test user
            test_user = User(username='test')
            test_user.set_password('test123')
            db.session.add(test_user)
            
            # Create sample events with rich content
            events = [
                Event(
                    title='Tech Conference 2024',
                    description='Join us for the most anticipated tech conference of the year! This event brings together industry leaders, innovators, and tech enthusiasts for three days of learning, networking, and inspiration. Featuring keynote speeches, panel discussions, and hands-on workshops.',
                    date=datetime(2024, 6, 15, 9, 0),
                    venue='Convention Center, New York',
                    category='Technology',
                    capacity=1000,
                    price=299.99,
                    organizer='Tech Innovators Inc.',
                    contact_email='info@techconference.com',
                    contact_phone='+1 (555) 123-4567',
                    requirements='Laptop, Notebook, Business Cards',
                    schedule='''Day 1:
- 9:00 AM: Registration
- 10:00 AM: Opening Keynote
- 12:00 PM: Lunch
- 2:00 PM: Panel Discussion
- 4:00 PM: Workshops
- 6:00 PM: Networking Reception

Day 2:
- 9:00 AM: Morning Sessions
- 12:00 PM: Lunch
- 2:00 PM: Breakout Sessions
- 5:00 PM: Closing Remarks''',
                    speakers='''John Smith - CEO, TechCorp
Sarah Johnson - CTO, InnovateX
Michael Brown - AI Researcher, Stanford
Emily Davis - Product Manager, Google''',
                    sponsors='''Platinum Sponsors:
- Microsoft
- Amazon Web Services
- Google Cloud

Gold Sponsors:
- IBM
- Oracle
- Salesforce''',
                    image_url='https://images.unsplash.com/photo-1511795409834-432f31197ce6?w=800&auto=format&fit=crop&q=60'
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
                    image_url='https://images.unsplash.com/photo-1470229722913-7c0e2dbbafd3?w=800&auto=format&fit=crop&q=60'
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
                    image_url='https://images.unsplash.com/photo-1552664730-d307ca884978?w=800&auto=format&fit=crop&q=60'
                )
            ]
            
            for event in events:
                db.session.add(event)
            
            db.session.commit()

if __name__ == '__main__':
    init_db()
    app.run(debug=True) 