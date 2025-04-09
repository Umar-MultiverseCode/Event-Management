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
        
        # Check if we need to add test user
        if not User.query.first():
            # Create a test user
            test_user = User(username='test')
            test_user.set_password('test123')
            db.session.add(test_user)
            db.session.commit()

if __name__ == '__main__':
    init_db()
    app.run(debug=True) 