import pymysql
pymysql.install_as_MySQLdb()
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from flask import Flask, render_template, request, redirect, session, flash, jsonify, make_response, url_for, send_from_directory
from flask_mysqldb import MySQL
from flask_session import Session
import datetime
import MySQLdb.cursors
from MySQLdb.cursors import DictCursor

import requests
import os
from datetime import timedelta
from math import ceil
from werkzeug.utils import secure_filename
from dotenv import load_dotenv
from functools import wraps
from datetime import date

# Load environment variables from .env file
load_dotenv()

# Initialize Flask application
app = Flask(__name__, static_folder='public', static_url_path='/public')
app.secret_key = 'FLASK_SECRET_KEY'  # Secret key for session management

# Session setup for Flask-Session
app.config['SESSION_TYPE'] = 'filesystem'
app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(
    minutes=15)  # Session expires after 15 minutes
Session(app)

# Upload folder configuration
UPLOAD_FOLDER = 'uploads'
ALLOWED_EXTENSIONS = {'pdf', 'docx', 'xlsx', 'png', 'jpg', 'jpeg', 'gif'}
if not os.path.exists(UPLOAD_FOLDER):
    os.makedirs(UPLOAD_FOLDER)  # Create upload folder if it doesn't exist
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER

# MySQL database configuration
app.config['MYSQL_HOST'] = os.getenv('MYSQL_HOST')
app.config['MYSQL_USER'] = os.getenv('MYSQL_USER')
app.config['MYSQL_PASSWORD'] = os.getenv('MYSQL_PASSWORD')
app.config['MYSQL_DB'] = os.getenv('MYSQL_DB')
app.config['MYSQL_PORT'] = int(os.getenv('MYSQL_PORT'))  # 🚨 REQUIRED
app.config['MYSQL_CURSORCLASS'] = 'DictCursor' # Return rows as dictionaries # Return rows as dictionaries
mysql = MySQL(app)

# SMTP Configuration - Loaded from .env file
app.config['SMTP_SERVER'] = os.getenv('SMTP_SERVER')
app.config['SMTP_PORT'] = int(os.getenv('SMTP_PORT', 587))
app.config['SMTP_USERNAME'] = os.getenv('SMTP_USERNAME')
app.config['SMTP_PASSWORD'] = os.getenv('SMTP_PASSWORD')
app.config['SENDER_EMAIL'] = os.getenv('SENDER_EMAIL')

# OpenRouter Configuration
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")


def send_email(recipient_email, subject, body, reply_to_email=None):

    #  HARD STOP for Render / prod toggle
    if os.getenv("EMAIL_ENABLED", "false").lower() != "true":
        print(" Email disabled by environment. Skipping send.")
        return

    # Existing config safety
    if not all([
        app.config.get('SMTP_SERVER'),
        app.config.get('SMTP_PORT'),
        app.config.get('SMTP_USERNAME'),
        app.config.get('SMTP_PASSWORD'),
        app.config.get('SENDER_EMAIL')
    ]):
        print(" SMTP settings missing. Skipping email.")
        return

    msg = MIMEMultipart()
    msg['From'] = app.config['SENDER_EMAIL']
    msg['To'] = recipient_email
    msg['Subject'] = subject

    if reply_to_email:
        msg.add_header('Reply-To', reply_to_email)

    msg.attach(MIMEText(body, 'html'))

    try:
        with smtplib.SMTP(
            app.config['SMTP_SERVER'],
            app.config['SMTP_PORT'],
            timeout=10   # ⏱️ extra safety
        ) as server:
            server.starttls()
            server.login(
                app.config['SMTP_USERNAME'],
                app.config['SMTP_PASSWORD']
            )
            server.send_message(msg)
            print(f" Email sent to {recipient_email}")

    except Exception as e:
        print(f" Email failed (ignored): {e}")


def summarize_with_openrouter(text_to_summarize):
    if not OPENROUTER_API_KEY:
        print("OpenRouter API key not set.")
        return "AI summary could not be generated."

    try:
        response = requests.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {OPENROUTER_API_KEY}",
                "HTTP-Referer": "http://localhost",
                "X-Title": "Task Summary Generator",
                "Content-Type": "application/json"
            },
            json={
                "model":  "deepseek/deepseek-r1-0528:free",
                "messages": [
                    {
                        "role": "system",
                        "content": "Summarize the following task update in a concise and professional manner and do not add extra messages from your side as I have to submit this summary to office."
                    },
                    {
                        "role": "user",
                        "content": text_to_summarize
                    }
                ],
                "temperature": 0.3
            },
            timeout=30
        )

        response.raise_for_status()
        data = response.json()
        return data["choices"][0]["message"]["content"].strip()

    except requests.exceptions.HTTPError as http_err:
        print(f"HTTP error occurred: {http_err}")
        print("Response content:", http_err.response.text)
        return f"HTTP error: {http_err.response.text}"

    except requests.exceptions.RequestException as req_err:
        print(f"Request error: {req_err}")
        return f"Request error: {req_err}"

    except Exception as e:
        print("Unexpected error:", e)
        return f"Unexpected error: {e}"


    

def allowed_file(filename):
    """
    Checks if a file's extension is allowed for upload.
    """
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS


@app.route('/uploads/<filename>')
def uploaded_file(filename):
    """
    Serves uploaded files from the UPLOAD_FOLDER.
    """
    return send_from_directory(app.config['UPLOAD_FOLDER'], filename)

app.secret_key = "super-secret-key-change-this"
app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(days=7)


@app.route('/')
def home():
    """
    Redirects to the dashboard if a user is logged in, otherwise to the login page.
    """
    if 'logged_in' in session:
        return redirect(url_for('dashboard', role=session['user_role']))
    flash('Please log in to access the dashboard.', 'info')
    return redirect(url_for('login'))


@app.route('/index', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        remember = request.form.get('remember')  # checkbox

        if not username or not password:
            flash('Please enter both username and password.', 'danger')
            return render_template('index.html')

        try:
            cursor = mysql.connection.cursor()
            cursor.execute("""
                SELECT id, name, role, email, department
                FROM users
                WHERE email = %s AND password = %s AND is_active = TRUE
            """, (username, password))
            user = cursor.fetchone()
            cursor.close()
        except Exception as e:
            flash(f"Database error: {e}", 'danger')
            return render_template('index.html')


        if user:
            # ✅ SESSION
            session['logged_in'] = True
            session['user_id'] = user['id']
            session['username'] = user['name']
            session['email'] = user['email']
            session['department'] = user['department']
            session['user_role'] = user['role'].strip().lower()

            flash(f"Welcome, {user['name']}!", 'success')

            # ✅ RESPONSE FOR COOKIES
            resp = make_response(
                redirect(url_for('dashboard', role=session['user_role']))
            )

            if remember:
                # Store email only (safe)
                resp.set_cookie(
                    'remember_email',
                    user['email'],
                    max_age=30 * 24 * 60 * 60,  # 30 days
                    httponly=True,
                    samesite='Lax'
                )
            else:
                resp.delete_cookie('remember_email')

            return resp

        flash('Invalid username or password or account is inactive.', 'danger')
        return render_template('index.html')

    return render_template('index.html')


@app.route('/dashboard/<role>')
def dashboard(role):
    """
    Dispatches users to their respective dashboards based on their role.
    Requires user to be logged in and role to match.
    """
    if 'logged_in' not in session or session.get('user_role') != role:
        flash('Unauthorized access. Please log in.', 'danger')
        return redirect(url_for('login'))

    if role == 'superadmin':
        return redirect(url_for('superadmin_dashboard'))
    elif role == 'head':
        return redirect(url_for('admin_dashboard'))
    elif role in ['employee', 'intern']:
        return redirect(url_for('employee_dashboard'))

    flash('Dashboard not found for your role.', 'danger')
    return redirect(url_for('login'))


@app.route('/logout')
def logout():
    """
    Logs out the user by clearing the session.
    """
    session.clear()
    flash('You have been successfully logged out.', 'success')
    return redirect(url_for('login'))


# ==============================================================================
# == DECORATORS & HELPERS
# ==============================================================================
def admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'logged_in' not in session or session.get('user_role') != 'head':
            flash('Unauthorized access. Please log in as an head.', 'danger')
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function


def get_dept_data(cursor, dept):
    """
    Gets ongoing task data for a department, using the latest submission status.
    """
    cursor.execute("""
        SELECT
            COUNT(DISTINCT at.id) AS count,
            GROUP_CONCAT(DISTINCT u.name SEPARATOR ', ') AS assigned_names
        FROM
            assigned_tasks at
        JOIN
            users u ON at.assigned_to_user_id = u.id
        LEFT JOIN (
            -- Subquery to get the status from the latest submission for each task
            SELECT
                s1.assigned_task_id,
                s1.status
            FROM
                submitted_tasks s1
            INNER JOIN (
                SELECT assigned_task_id, MAX(created_at) AS max_created_at
                FROM submitted_tasks
                WHERE assigned_task_id IS NOT NULL
                GROUP BY assigned_task_id
            ) s2 ON s1.assigned_task_id = s2.assigned_task_id AND s1.created_at = s2.max_created_at
        ) latest_submission ON at.id = latest_submission.assigned_task_id
        WHERE
            u.department = %s
            AND COALESCE(latest_submission.status, at.status) IN ('Ongoing', 'In Progress', 'Pending')
    """, (dept,))
    result = cursor.fetchone()
    return result if result else {"count": 0, "assigned_names": ""}

# get project data

def get_dept_projects(cur, department):
    """
    Fetches ongoing projects for a given department.
    Returns a dict with count and comma-separated project names.
    """
    cur.execute("""
        SELECT 
            COUNT(*) AS count,
            GROUP_CONCAT(name SEPARATOR ', ') AS project_names
        FROM projects
        WHERE department = %s AND status IN ('Ongoing', 'In Progress', 'Onhold')
    """, (department,))
    result = cur.fetchone()
    return {
        'count': result['count'] if result['count'] else 0,
        'project_names': result['project_names'] if result['project_names'] else ''
    }


#get head wise project details
def get_head_projects(cur, head_id, statuses):
    placeholders = ','.join(['%s'] * len(statuses))

    query = f"""
        SELECT 
            COUNT(*) AS count,
            GROUP_CONCAT(name SEPARATOR ', ') AS project_names
        FROM projects
        WHERE project_head_id = %s
          AND status IN ({placeholders})
    """

    cur.execute(query, (head_id, *statuses))
    result = cur.fetchone()

    return {
        'count': result['count'] or 0,
        'project_names': result['project_names'] or ''
    }
#get head wise task details
def get_head_tasks(cur, head_id):
    query = """
        SELECT 
            at.id,
            at.task_name,
            at.status,
            at.created_at,
            at.due_date,
            u.name AS assigned_to,
            p.name AS project_name,
            st.created_at AS completed_date
        FROM assigned_tasks at
        JOIN projects p 
            ON at.project_id = p.id
        LEFT JOIN users u 
            ON at.assigned_to_user_id = u.id
        LEFT JOIN submitted_tasks st
            ON st.assigned_task_id = at.id
            AND st.status = 'Completed'
        WHERE p.project_head_id = %s order by at.due_date desc
    """
    cur.execute(query, (head_id,))
    return cur.fetchall()

# get data of a busy or free user
def get_busy_user_ids(cursor):
    # Today's date
    cursor.execute("SELECT CURDATE() AS today")
    today = cursor.fetchone()['today']

    cursor.execute("""
        SELECT DISTINCT assigned_to_user_id
        FROM assigned_tasks
        WHERE status IN ('Pending', 'Ongoing', 'In Progress')
        AND due_date >= %s
    """, (today,))

    rows = cursor.fetchall()

    busy = {str(row['assigned_to_user_id']) for row in rows}
    return busy

def get_dept_projects(cur, department, statuses):
    query = """
    SELECT 
        p.id AS project_id,
        p.name AS project_name,
        p.department,
        p.created_at,
        p.deadline_date,
        p.status,
        u.name AS project_head,

        (
            SELECT GROUP_CONCAT(name SEPARATOR ', ')
            FROM users
            WHERE FIND_IN_SET(
                id,
                (
                    SELECT user_ids
                    FROM project_members
                    WHERE project_id = p.id
                    LIMIT 1
                )
            )
        ) AS members

    FROM projects p
    JOIN users u ON p.project_head_id = u.id
    WHERE p.department = %s
      AND p.status IN %s
    ORDER BY p.deadline_date ASC
    """
    cur.execute(query, (department, tuple(statuses)))
    return cur.fetchall()

#Get all task related data for head dashboard

def get_dept_tasks(cur, department):
    query = """
        SELECT 
            at.id,
            at.task_name,
            at.status,
            at.created_at,
            at.due_date,
            u.name AS assigned_to,
            p.name AS project_name,
            st.created_at AS completed_date
        FROM assigned_tasks at
        LEFT JOIN users u 
            ON at.assigned_to_user_id = u.id
        LEFT JOIN projects p 
            ON at.project_id = p.id
        LEFT JOIN submitted_tasks st
            ON st.assigned_task_id = at.id
            AND st.status = 'Completed'
        WHERE at.department = %s
    """
    cur.execute(query, (department,))
    return cur.fetchall()





# ==============================================================================
# == SUPERADMIN ROUTES
# ==============================================================================


@app.route('/superadmin/users')
def superadmin_users():
    """
    Placeholder route for superadmin users page.
    """
    if 'logged_in' not in session or session.get('user_role') != 'superadmin':
        return redirect(url_for('login'))
    return render_template('superadmin/users.html')  # Corrected Path


@app.route('/superadmin/dashboard')
def superadmin_dashboard():
    """
    Renders the superadmin dashboard with various statistics.
    """
    if 'logged_in' not in session or session.get('user_role') != 'superadmin':
        flash('Unauthorized access. Please log in.', 'danger')
        return redirect(url_for('login'))
    
    today = date.today()
    due_soon_limit = today + timedelta(days=5)

    cur = mysql.connection.cursor()
    cur.execute("SELECT * FROM assigned_tasks")
    tasks = cur.fetchall()

    cur.execute("SELECT * FROM projects")
    projects = cur.fetchall()

    


    # Department-wise projects
    sales_projects = get_dept_projects(
        cur, 'Sales', ['Ongoing', 'Completed', 'Onhold']
    )

    developer_projects = get_dept_projects(
        cur, 'Developer', ['Ongoing', 'Completed', 'Onhold']
    )

    graphics_projects = get_dept_projects(
        cur, 'Graphics', ['Ongoing', 'Completed', 'Onhold']
    )

    digital_projects = get_dept_projects(
        cur, 'Digital', ['Ongoing', 'Completed', 'Onhold']
    )
    
    cur.execute("SELECT COUNT(*) as count FROM submitted_tasks")
    submitted_tasks_count = cur.fetchone()['count']

    cur.execute("""
        SELECT COUNT(DISTINCT user_id) AS count
        FROM today_work
        WHERE work_date = %s
    """, (today,))

    today_work_count = cur.fetchone()['count']
    


    
    cur.close()

    return render_template('superadmin/dashboard.html',
                           name=session['username'],
                           tasks=tasks,
                           projects=projects,
                           due_soon_limit=due_soon_limit,
                           submitted_tasks_count=submitted_tasks_count,
                           today=today,
                           today_work_count=today_work_count,
                           sales_projects=sales_projects,
                           digital_projects=digital_projects,
                           developer_projects=developer_projects,
                           graphics_projects=graphics_projects,
                           user_role=session.get('user_role'),
                           email=session.get('email'))
# ongoing_projects section

@app.route('/superadmin/ongoing_projects/<department>')
def superadmin_ongoing_projects(department):
    if 'logged_in' not in session or session.get('user_role') != 'superadmin':
        flash('Unauthorized access. Please log in as Superadmin.', 'danger')
        return redirect(url_for('login'))

    cur = mysql.connection.cursor()

    cur.execute("""
       SELECT 
    p.id AS project_id,
    p.name AS project_name,
    p.department,
    p.description,
    p.created_at,
    p.deadline_date,
    p.status,
    u.name AS project_head,

    -- NEW: fetch names of all members
    (
        SELECT GROUP_CONCAT(name SEPARATOR ', ')
        FROM users 
        WHERE FIND_IN_SET(id, (
            SELECT user_ids FROM project_members WHERE project_id = p.id LIMIT 1
        ))
    ) AS members

FROM projects p
JOIN users u ON p.project_head_id = u.id
WHERE 
    p.department = %s
    AND p.status IN ('Ongoing', 'In Progress', 'Onhold')
ORDER BY 
    p.deadline_date ASC;

    """, (department,))

    projects = cur.fetchall()
    cur.close()

    for project in projects:
        if isinstance(project['deadline_date'], datetime.date):
            project['deadline_date'] = project['deadline_date'].strftime('%d/%m/%Y')
        if isinstance(project['created_at'], datetime.date):
            project['created_at'] = project['created_at'].strftime('%d/%m/%Y')
    print("Fetched Projects:", projects)


    return render_template('superadmin/ongoing_projects.html', projects=projects, department=department,data=[],user_role="superadmin")

@app.route('/debug/projects')
def debug_projects():
    cur = mysql.connection.cursor()
    cur.execute("SELECT * FROM projects LIMIT 20")  # limit for safety
    projects = cur.fetchall()
    cur.close()

    # Pretty print in console
    for p in projects:
        print(p)

    return "Projects printed in console ✅"

# ongoing task section


@app.route('/superadmin/ongoing_tasks/<department>')
def superadmin_ongoing_tasks(department):
    if 'logged_in' not in session or session.get('user_role') not in ['superadmin', 'head']:
        flash('Unauthorized access. Please log in.', 'danger')
        return redirect(url_for('login'))

    cur = mysql.connection.cursor()
    cur.execute("""
        SELECT
            at.task_name,
            at.due_date,
            COALESCE(latest_submission.status, at.status) AS status, -- Use latest submission status or default
            u.name AS assigned_to_name
        FROM
            assigned_tasks at
        JOIN
            users u ON at.assigned_to_user_id = u.id
        LEFT JOIN (
            -- Subquery to get the status from the latest submission for each task
            SELECT
                s1.assigned_task_id,
                s1.status
            FROM
                submitted_tasks s1
            INNER JOIN (
                SELECT assigned_task_id, MAX(created_at) AS max_created_at
                FROM submitted_tasks
                WHERE assigned_task_id IS NOT NULL
                GROUP BY assigned_task_id
            ) s2 ON s1.assigned_task_id = s2.assigned_task_id AND s1.created_at = s2.max_created_at
        ) latest_submission ON at.id = latest_submission.assigned_task_id
        WHERE
            u.department = %s
            AND COALESCE(latest_submission.status, at.status) IN ('Ongoing', 'In Progress', 'Pending')
        ORDER BY
            at.due_date ASC
    """, (department,))
    tasks = cur.fetchall()
    cur.close()

    for task in tasks:
        if isinstance(task['due_date'], datetime.date):
            task['due_date'] = task['due_date'].strftime('%d/%m/%Y')

    # Corrected Path
    return render_template('superadmin/ongoing_tasks.html', tasks=tasks, department=department)


@app.route('/superadmin/submitted_tasks')
def superadmin_submitted_tasks():
    if 'logged_in' not in session or session.get('user_role') != 'superadmin':
        flash('Unauthorized access. Please log in.', 'danger')
        return redirect(url_for('login'))

    project_id_filter = request.args.get('project_id', type=int)

    cur = mysql.connection.cursor()
    try:
        # Base query
        query = """
            SELECT
                st.id, st.task_description, st.status, st.attachment, st.ai_summary,
                st.created_at as submitted_date,
                u.name as employee_name,
                at.task_name,
                p.name as project_name
            FROM submitted_tasks st
            JOIN users u ON st.user_id = u.id
            LEFT JOIN assigned_tasks at ON st.assigned_task_id = at.id
            LEFT JOIN projects p ON at.project_id = p.id
        """
        params = []

        # Add filter if project_id is provided
        if project_id_filter:
            query += " WHERE p.id = %s"
            params.append(project_id_filter)

        query += " ORDER BY st.created_at DESC"

        cur.execute(query, params)
        submitted_tasks = cur.fetchall()

        for task in submitted_tasks:
            if task.get('submitted_date') and isinstance(task['submitted_date'], (datetime.date, datetime.datetime)):
                task['submitted_date'] = task['submitted_date'].strftime(
                    '%d/%m/%Y')

        # Fetch all projects for the filter dropdown
        cur.execute("SELECT id, name FROM projects ORDER BY name")
        projects = cur.fetchall()

    except Exception as e:
        flash(f"Error fetching submitted tasks: {e}", "danger")
        submitted_tasks = []
        projects = []
    finally:
        cur.close()

    return render_template('superadmin/submitted_tasks.html',
                           submitted_tasks=submitted_tasks,
                           projects=projects,
                           selected_project=project_id_filter)


@app.route('/add_user', methods=['POST'])
def add_user():
    if 'logged_in' not in session or session['user_role'] not in ['superadmin', 'head']:
        flash("Unauthorized access", "danger")
        return redirect(url_for('login'))

    # Capitalize the user's name
    name = request.form.get("name", "").title()
    email = request.form.get("email")
    password = request.form.get("password")
    role = request.form.get("role")

    # MODIFICATION: Determine department and assigned_head based on user role
    if session['user_role'] == 'head':
        department = session.get('department')
        # Admin is automatically the head
        assigned_head = session.get('user_id')
    else:  # This block is for 'superadmin'
        department = request.form.get("department")
        assigned_head = request.form.get("assigned_head")

    try:
        cursor = mysql.connection.cursor()
        cursor.execute("SELECT id FROM users WHERE email = %s and is_active=1", (email,))
        if cursor.fetchone():
            flash(f"❌ User with email {email} already exists.", "danger")
            if session['user_role'] == 'head':
                return redirect(url_for('admin_adduser'))
            return redirect(url_for('superadmin_adduser'))

        assigned_head_db = assigned_head if assigned_head else None
        cursor.execute("""
            INSERT INTO users (name, email, password, role, department, assigned_head, is_active)
            VALUES (%s, %s, %s, %s, %s, %s, TRUE)
        """, (name, email, password, role, department, assigned_head_db))
        mysql.connection.commit()
        flash("✅ New user added successfully!", "success")
    except Exception as e:
        mysql.connection.rollback()
        flash(f"❌ Error adding user: {e}", "danger")
    finally:
        cursor.close()

    if session['user_role'] == 'head':
        return redirect(url_for('admin_adduser'))
    return redirect(url_for('superadmin_adduser'))


@app.route('/superadmin/adduser')
def superadmin_adduser():
    if 'logged_in' not in session or session.get('user_role') != 'superadmin':
        return redirect(url_for('login'))

    cursor = mysql.connection.cursor()
    cursor.execute(
        "SELECT id, name, email, role, department FROM users WHERE is_active = TRUE AND (role = 'head' OR role = 'superadmin')")
    all_potential_heads = cursor.fetchall()
    cursor.close()
    # Corrected Path
    return render_template('superadmin/adduser.html', all_users=all_potential_heads)

# addproject section
@app.route('/add_project', methods=['POST'])
def add_project():
    if 'logged_in' not in session or session['user_role'] not in ['superadmin', 'head']:
        flash("Unauthorized access", "danger")
        return redirect(url_for('login'))

    # Form data
    project_name = request.form.get("project_name", "").title()
    description = request.form.get("description")
    project_head_id = request.form.get("project_head_ids")
    department = request.form.get("department")
    start_date = request.form.get("start_date")
    deadline_date = request.form.get("deadline_date")

    # MULTIPLE employees from dropdown
    user_ids = request.form.getlist("project_member_ids")  
    # example => ["3", "6", "9"]

    try:
        cursor = mysql.connection.cursor()

        # 🔍 1️⃣ CHECK DUPLICATE PROJECT (name + department)
        cursor.execute("""
            SELECT id FROM projects
            WHERE name = %s AND department = %s
        """, (project_name, department))

        existing_project = cursor.fetchone()

        if existing_project:
            flash(
                f"⚠️ Project '{project_name}' already exists in {department} department.",
                "warning"
            )
            return redirect(url_for('superadmin_createproject'))

        # 1️⃣ Insert project
        cursor.execute("""
            INSERT INTO projects 
                (name, description, project_head_id, department, created_at, deadline_date)
            VALUES (%s, %s, %s, %s, %s, %s)
        """, (project_name, description, project_head_id, department, start_date, deadline_date))
        
        mysql.connection.commit()

        # 2️⃣ Get created project's ID
        project_id = cursor.lastrowid

        # 3️⃣ Convert employee list → "3,6,9"
        if user_ids:
            user_ids_str = ",".join(user_ids)

            # 4️⃣ Insert into project_members table
            cursor.execute("""
                INSERT INTO project_members (project_id, user_ids)
                VALUES (%s, %s)
            """, (project_id, user_ids_str))

            mysql.connection.commit()

        flash("✅ New project created successfully!", "success")

    except Exception as e:
        mysql.connection.rollback()
        flash(f"❌ Error creating project: {e}", "danger")

    finally:
        cursor.close()

    # Redirect back
    if session['user_role'] == 'head':
        return redirect(url_for('admin_createproject'))
    
    return redirect(url_for('superadmin_createproject'))


# superadmin createproject section

@app.route('/superadmin/createproject')
def superadmin_createproject():
    if 'logged_in' not in session or session['user_role'] != 'superadmin':
        return redirect(url_for('login'))

    cursor = mysql.connection.cursor()
    cursor.execute(
        "SELECT id, name, email, department FROM users WHERE (role = 'head' OR role = 'superadmin') AND is_active = TRUE")
    admins = cursor.fetchall()
    cursor.execute("SELECT id, name, role, assigned_head,department FROM users WHERE role IN ('employee','intern') AND is_active=TRUE")

    
    members = cursor.fetchall()
    busy_members = get_busy_user_ids(cursor)
    cursor.close()
    today_date = datetime.date.today().strftime('%Y-%m-%d')
    # Corrected Path
    return render_template('superadmin/createproject.html', admins=admins,
                            today_date=today_date,
                            members=members,
                            busy_members=busy_members)


@app.route('/superadmin/viewprojects')
def superadmin_viewprojects():
    if 'logged_in' not in session or session['user_role'] != 'superadmin':
        flash('Unauthorized access. Please log in.', 'danger')
        return redirect(url_for('login'))

    page = request.args.get('page', 1, type=int)
    per_page = 10
    status_filter = request.args.get('status')

    cur = mysql.connection.cursor()
    try:
        # Count projects
        if status_filter:
            cur.execute("SELECT COUNT(id) as count FROM projects WHERE status = %s", (status_filter,))
        else:
            cur.execute("SELECT COUNT(id) as count FROM projects")
        total_projects = cur.fetchone()['count']

        total_pages = ceil(total_projects / per_page)
        offset = (page - 1) * per_page

        # Fetch projects
        if status_filter:
            cur.execute("""
                SELECT p.id, p.name, p.description, p.department, p.created_at, 
                       p.deadline_date, p.status AS status, p.project_head_id,
                       u.name AS project_head_name
                FROM projects p
                LEFT JOIN users u ON p.project_head_id = u.id
                WHERE p.status = %s
                ORDER BY p.id DESC
                LIMIT %s OFFSET %s
            """, (status_filter, per_page, offset))
        else:
            cur.execute("""
                SELECT p.id, p.name, p.description, p.department, p.created_at, 
                       p.deadline_date, p.status AS status, p.project_head_id,
                       u.name AS project_head_name
                FROM projects p
                LEFT JOIN users u ON p.project_head_id = u.id
                ORDER BY p.id DESC
                LIMIT %s OFFSET %s
            """, (per_page, offset))

        projects = cur.fetchall()

        # Fetch admins
        cur.execute("""
            SELECT id, name, email, department 
            FROM users 
            WHERE (role = 'head' OR role = 'superadmin') AND is_active = TRUE
        """)
        admins = cur.fetchall()

        cur.execute("SELECT id, name, role, department FROM users WHERE role IN ('employee','intern') AND is_active=TRUE")
        members = cur.fetchall()
        busy_members = get_busy_user_ids(cur)

        # Attach date formatting + members
        for project in projects:
            created_at_date = project.get('created_at')
            deadline_date_date = project.get('deadline_date')

            project['created_at'] = created_at_date.strftime('%d/%m/%Y') if created_at_date else 'N/A'
            project['deadline_date'] = deadline_date_date.strftime('%d/%m/%Y') if deadline_date_date else 'N/A'
            project['created_at_raw'] = created_at_date.strftime('%Y-%m-%d') if created_at_date else ''
            project['deadline_date_raw'] = deadline_date_date.strftime('%Y-%m-%d') if deadline_date_date else ''

            # Fetch project members
            cur.execute("SELECT user_ids FROM project_members WHERE project_id = %s", (project["id"],))
            row = cur.fetchone()

            project["member_ids_list"] = row["user_ids"].split(",") if row and row["user_ids"] else []

    except Exception as e:
        flash(f"Error fetching projects: {e}", "danger")
        projects, admins, total_pages = [], [], 1

    finally:
        cur.close()

    return render_template('superadmin/viewproject.html',
                           projects=projects,
                           admins=admins,
                           members=members,
                           busy_members=busy_members,
                           current_page=page,
                           total_pages=total_pages,
                           endpoint='superadmin_viewprojects',
                           selected_status=status_filter)




@app.route('/add_task', methods=['POST'])
def add_task():
    if 'logged_in' not in session or session['user_role'] not in ['superadmin', 'head']:
        flash("Unauthorized access", "danger")
        return redirect(url_for('login'))

    # Capitalize the task name
    task_name = request.form.get("task_name", "").title()
    task_description = request.form.get("task_description")
    assigned_to_user_ids = request.form.getlist("assigned_to_user_ids")
    due_date = request.form.get("due_date")
    project_id = request.form.get("project_id_task") or None
    assigned_by_user_id = session.get('user_id')

    try:
        cursor = mysql.connection.cursor()
        if not assigned_to_user_ids:
            flash("❌ No employees selected.", "warning")
            if session['user_role'] == 'head':
                return redirect(url_for('admin_createtask'))
            elif session['user_role']=='employee':
                return redirect(url_for('employee_createtask'))
            else:
                return redirect(url_for('superadmin_createtask'))

        for user_id in assigned_to_user_ids:
            cursor.execute(
                "SELECT email, department, name FROM users WHERE id = %s", (user_id,))
            user_data = cursor.fetchone()
            if user_data:
                cursor.execute("""
                    INSERT INTO assigned_tasks (assigned_by_user_id, assigned_to_user_id, assigned_to_email, task_name, task_description, due_date, status, project_id, department, date_assigned, time_assigned)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, CURDATE(), CURTIME())
                """, (assigned_by_user_id, user_id, user_data['email'], task_name, task_description, due_date, 'Pending', project_id, user_data['department']))

                # Send email notification
                subject = "New Task Assigned"
                body = f"""
                <html>
                <body>
                    <p>Hello {user_data['name']},</p>
                    <p>A new task has been assigned to you:</p>
                    <p><b>Task:</b> {task_name}</p>
                    <p><b>Description:</b> {task_description}</p>
                    <p><b>Due Date:</b> {due_date}</p>
                    <p>Please log in to the dashboard to view the details.</p>
                </body>
                </html>
                """
                send_email(user_data['email'], subject, body)

        mysql.connection.commit()
        flash("✅ New task(s) assigned successfully!", "success")
    except Exception as e:
        mysql.connection.rollback()
        flash(f"❌ Error assigning task: {e}", "danger")
    finally:
        cursor.close()

    if session['user_role'] == 'head':
        return redirect(url_for('admin_createtask'))
    elif session['user_role']=='employee':
        return redirect(url_for('employee_createtask'))
    else:
        return redirect(url_for('superadmin_createtask'))


@app.route('/superadmin/createtask')
def superadmin_createtask():
    if 'logged_in' not in session or session['user_role'] != 'superadmin':
        flash('Unauthorized access. Please log in.', 'danger')
        return redirect(url_for('login'))

    cur = mysql.connection.cursor()
    try:
        cur.execute("SELECT id, name, email, department FROM users WHERE (role = 'head') AND is_active = TRUE")
        admins = cur.fetchall()
        cur.execute("SELECT id, name, department,status FROM projects ORDER BY name")
        projects = cur.fetchall()
    except Exception as e:
        flash(f"Error fetching data: {e}", "danger")
        admins, projects = [], []
    finally:
        cur.close()

    today_date = datetime.date.today().strftime('%Y-%m-%d')
    # Corrected Path
    return render_template('superadmin/createtask.html', admins=admins, projects=projects, today_date=today_date)


@app.route('/superadmin/viewtasks')
def superadmin_viewtasks():
    if 'logged_in' not in session or session['user_role'] != 'superadmin':
        flash('Unauthorized access. Please log in.', 'danger')
        return redirect(url_for('login'))

    page = request.args.get('page', 1, type=int)
    per_page = 15  # Number of tasks per page
    
    # Ensure page is at least 1
    if page < 1:
        page = 1

    cur = mysql.connection.cursor()
    try:
        # Get total number of tasks for pagination
        cur.execute("SELECT COUNT(id) as count FROM assigned_tasks")
        total_tasks = cur.fetchone()['count']
        total_pages = ceil(total_tasks / per_page)

        # Calculate the offset for the query
        offset = (page - 1) * per_page

        # Fetch tasks for the current page
        cur.execute("""
            SELECT at.id, at.task_name, at.task_description, at.due_date, at.status, at.project_id, at.department, at.date_assigned, at.time_assigned, at.assigned_to_user_id, u.name AS assigned_to_name, u.role, p.name AS project
            FROM assigned_tasks at
            LEFT JOIN users u ON at.assigned_to_user_id = u.id
            LEFT JOIN projects p ON at.project_id = p.id
            ORDER BY at.date_assigned DESC, at.time_assigned DESC
            LIMIT %s OFFSET %s
        """, (per_page, offset))
        tasks = cur.fetchall()

        cur.execute("SELECT id, name, department FROM projects ORDER BY name")
        projects = cur.fetchall()

        for task in tasks:
            task['assigned_date_formatted'] = task['date_assigned'].strftime(
                '%d/%m/%Y') if task.get('date_assigned') else 'N/A'
            task['time_assigned_formatted'] = str(
                task['time_assigned']) if task.get('time_assigned') else 'N/A'
            if task.get('due_date'):
                task['due_date_iso'] = task['due_date'].strftime('%Y-%m-%d')   # for input
                task['due_date_display'] = task['due_date'].strftime('%d/%m/%Y')  # for humans
            else:
                task['due_date_iso'] = ''
                task['due_date_display'] = ''

    except Exception as e:
        flash(f"Error fetching tasks: {e}", "danger")
        tasks, projects, total_pages = [], [], 1
    finally:
        cur.close()


    return render_template('superadmin/viewtasks.html',
                           tasks=tasks,
                           projects=projects,
                           current_page=page,
                           total_pages=total_pages,
                           endpoint='superadmin_viewtasks')

@app.route('/superadmin/weekly_report', methods=['GET'])
def superadmin_weekly_report():

    # ------------------ SESSION DATA ------------------
    user_role = session.get('user_role')
    user_id = session.get('user_id')

    # ------------------ FILTER PARAMS ------------------
    department = request.args.get('department')
    head_id = request.args.get('head_id', type=int)
    employee_id = request.args.get('employee_id', type=int)
    from_date = request.args.get('from_date')
    to_date = request.args.get('to_date')

    cursor = mysql.connection.cursor(MySQLdb.cursors.DictCursor)

    # ------------------ MAIN REPORT QUERY ------------------
    query = """
    SELECT 
        u.name AS employee_name,
        u.department,
        DATE_FORMAT(MIN(s.created_at), '%%Y-%%m-%%d') AS from_date,
        DATE_FORMAT(MAX(s.created_at), '%%Y-%%m-%%d') AS to_date,
        GROUP_CONCAT(
            CONCAT(
                s.ai_summary,
                '||',
                DATE_FORMAT(s.created_at, '%%d %%b %%Y'),
                '|| Task: ',
                COALESCE(at.task_name, 'N/A'),
                '|| Project: ',
                COALESCE(p.name, 'N/A')
            )
            SEPARATOR '\n'
        ) AS weekly_summary
    FROM submitted_tasks s
    JOIN users u ON s.user_id = u.id
    LEFT JOIN assigned_tasks at ON s.assigned_task_id = at.id
    LEFT JOIN projects p ON p.id = COALESCE(s.project_id, at.project_id)
    WHERE 1=1
"""

    params = []

    # 🔐 Head can see only their employees
    if user_role == 'head':
        query += " AND u.assigned_head = %s"
        params.append(user_id)

    if department:
        query += " AND u.department = %s"
        params.append(department)

    if head_id:
        query += " AND u.assigned_head = %s"
        params.append(head_id)

    if employee_id:
        query += " AND u.id = %s"
        params.append(employee_id)

    if from_date and to_date:
        query += " AND DATE(s.created_at) BETWEEN %s AND %s"
        params.extend([from_date, to_date])

    query += " GROUP BY u.id ORDER BY u.department, u.name"

    cursor.execute(query, tuple(params))
    weekly_reports = cursor.fetchall()

    # ------------------ DEPARTMENTS ------------------
    cursor.execute("SELECT DISTINCT department FROM users WHERE department IS NOT NULL")
    departments = [row['department'] for row in cursor.fetchall()]

    # ------------------ HEADS ------------------
    heads = []
    if user_role == 'superadmin':
        head_query = "SELECT id, name FROM users WHERE role = 'head' AND is_active=True"
        head_params = []
        if department:
            head_query += " AND department = %s"
            head_params.append(department)

        cursor.execute(head_query, tuple(head_params))
        heads = cursor.fetchall()

    # ------------------ EMPLOYEES ------------------
    emp_query = """
        SELECT id, name 
        FROM users 
        WHERE role IN ('employee','intern') AND is_active=True
    """
    emp_params = []

    if user_role == 'superadmin':
        if head_id:
            emp_query += " AND assigned_head = %s"
            emp_params.append(head_id)
        if department:
            emp_query += " AND department = %s"
            emp_params.append(department)

    elif user_role == 'head':
        emp_query += " AND assigned_head = %s"
        emp_params.append(user_id)

    cursor.execute(emp_query, tuple(emp_params))
    employees = cursor.fetchall()

    cursor.close()

    return render_template(
        'superadmin/weekly_report.html',
        weekly_reports=weekly_reports,
        departments=departments,
        heads=heads,
        employees=employees,
        selected_department=department,
        selected_head=head_id,
        selected_employee=employee_id,
        from_date=from_date,
        to_date=to_date
    )

# @app.route('/admin/weekly_report', methods=['GET'])
# @admin_required
# def admin_weekly_report():

#     department = session.get('department')  # fixed department
#     from_date = request.args.get('from_date')
#     to_date = request.args.get('to_date')

#     cursor = mysql.connection.cursor(MySQLdb.cursors.DictCursor)

#     query = """
#               SELECT 
#     u.name AS employee_name,
#     u.department,

#     DATE_FORMAT(MIN(s.created_at), '%%Y-%%m-%%d') AS from_date,
#     DATE_FORMAT(MAX(s.created_at), '%%Y-%%m-%%d') AS to_date,

#     GROUP_CONCAT(
#     CONCAT(
#         s.ai_summary,
#         '<<<SEP>>>',
#         DATE_FORMAT(s.created_at, '%%d/%%m/%%Y'),
#         '<<<SEP>>>task: ',
#         COALESCE(at.task_name, 'N/A'),
#         '<<<SEP>>>project: ',
#         COALESCE(p.name, 'N/A')
#     )
#     ORDER BY s.created_at
#     SEPARATOR '\n'
# ) AS weekly_summary


# FROM submitted_tasks s
# JOIN users u ON s.user_id = u.id
# LEFT JOIN assigned_tasks at ON s.assigned_task_id = at.id
# LEFT JOIN projects p 
#     ON p.id = COALESCE(s.project_id, at.project_id)

# WHERE u.department = %s


#     """

#     params = [department]

#     # 📅 Date range filter
#     if from_date and to_date:
#         query += " AND DATE(s.created_at) BETWEEN %s AND %s"
#         params.extend([from_date, to_date])

#     query += " GROUP BY u.id, u.name, u.department ORDER BY u.name"

#     print("Admin Query:", query)
#     print("Params:", params)

#     cursor.execute(query, tuple(params))
#     weekly_reports = cursor.fetchall()
#     cursor.close()

#     return render_template(
#         'superadmin/weekly_report.html',  # reuse same UI
#         weekly_reports=weekly_reports,
#         selected_department=department,
#         from_date=from_date,
#         to_date=to_date,
#         departments=[department]  # admin sees only own dept
#     )





@app.route('/update_project', methods=['POST'])
def update_project():
    if 'logged_in' not in session or session['user_role'] not in ['superadmin', 'head']:
        flash("Unauthorized access", "danger")
        return redirect(url_for('login'))

    project_id = request.form.get("project_id")

    # Project fields
    project_name = request.form.get("project_name", "").title()
    description = request.form.get("description")
    department = request.form.get("department")
    project_head_id = request.form.get("project_head_id")
    start_date = request.form.get("start_date")
    deadline_date = request.form.get("deadline_date")
    update_status = request.form.get("status")

    # Project members (list -> comma string)
    project_members = request.form.getlist("project_member_ids")
    project_members_str = ",".join(project_members)

    try:
        cursor = mysql.connection.cursor()

        # UPDATE PROJECT
        cursor.execute("""
            UPDATE projects 
            SET name = %s, description = %s, department = %s, project_head_id = %s, 
                created_at = %s, deadline_date = %s, status = %s
            WHERE id = %s
        """, (project_name, description, department, project_head_id,
              start_date, deadline_date, update_status, project_id))

        # UPDATE / INSERT PROJECT MEMBERS
        cursor.execute("SELECT id FROM project_members WHERE project_id = %s", (project_id,))
        existing = cursor.fetchone()

        if existing:
            # update existing row
            cursor.execute("""
                UPDATE project_members 
                SET user_ids = %s 
                WHERE project_id = %s
            """, (project_members_str, project_id))
        else:
            # insert new row
            cursor.execute("""
                INSERT INTO project_members (project_id, user_ids)
                VALUES (%s, %s)
            """, (project_id, project_members_str))

        mysql.connection.commit()
        flash("Project & Members updated successfully!", "success")

    except Exception as e:
        mysql.connection.rollback()
        flash(f" Error updating project: {e}", "danger")

    finally:
        cursor.close()

    if session['user_role'] == 'head':
        return redirect(url_for('admin_viewprojects'))
    return redirect(url_for('superadmin_viewprojects'))



@app.route('/delete_project/<int:project_id>', methods=['POST'])
def delete_project(project_id):
    if 'logged_in' not in session or session['user_role'] not in ['superadmin', 'head']:
        return jsonify({"success": False, "message": "Unauthorized"}), 403

    try:
        cursor = mysql.connection.cursor()
        cursor.execute(
            "SELECT COUNT(*) AS count FROM assigned_tasks WHERE project_id = %s", (project_id,))
        if cursor.fetchone()['count'] > 0:
            return jsonify({"success": False, "message": "Cannot delete project with associated tasks."}), 400

        cursor.execute("DELETE FROM projects WHERE id = %s", (project_id,))
        mysql.connection.commit()
        flash(" Project deleted successfully!", "success")
        return jsonify({"success": True, "message": "Project deleted."})
    except Exception as e:
        mysql.connection.rollback()
        flash(f" Error deleting project: {e}", "danger")
        return jsonify({"success": False, "message": f"An error occurred: {e}"}), 500
    finally:
        cursor.close()


@app.route('/delete_task/<int:task_id>')
def delete_task(task_id):
    if 'logged_in' not in session or session['user_role'] not in ['superadmin', 'head']:
        flash("Unauthorized access", "danger")
        return redirect(url_for('login'))

    try:
        cursor = mysql.connection.cursor()
        cursor.execute("DELETE FROM assigned_tasks WHERE id = %s", (task_id,))
        mysql.connection.commit()
        flash("✅ Task deleted successfully!", "success")
    except Exception as e:
        mysql.connection.rollback()
        flash(f"❌ Error deleting task: {e}", "danger")
    finally:
        cursor.close()

    if session['user_role'] == 'head':
        return redirect(url_for('admin_viewtasks'))
    return redirect(url_for('superadmin_viewtasks'))


@app.route('/update_task', methods=['POST'])
def update_task():
    if 'logged_in' not in session or session['user_role'] not in ['superadmin', 'head']:
        flash("Unauthorized access", "danger")
        return redirect(url_for('login'))

    task_id = request.form.get("task_id")
    # Capitalize the task name
    task_name = request.form.get("task_name", "").title()
    task_description = request.form.get("task_description")
    due_date = request.form.get("due_date")
    status = request.form.get("status")
    project_id = request.form.get("project_id") or None

    try:
        cursor = mysql.connection.cursor()
        cursor.execute("""
            UPDATE assigned_tasks SET task_name = %s, task_description = %s, due_date = %s, status = %s, project_id = %s
            WHERE id = %s
        """, (task_name, task_description, due_date, status, project_id, task_id))
        mysql.connection.commit()
        flash("✅ Task updated successfully!", "success")
    except Exception as e:
        mysql.connection.rollback()
        flash(f"❌ Error updating task: {e}", "danger")
    finally:
        cursor.close()

    if session['user_role'] == 'head':
        return redirect(url_for('admin_viewtasks'))
    return redirect(url_for('superadmin_viewtasks'))


@app.route('/systemaccess')
def systemaccess():

    if 'logged_in' not in session:
        flash('Unauthorized access. Please log in.', 'danger')
        return redirect(url_for('login'))

    user_role = session.get('user_role')
    department = session.get('department')
    head_id = session.get('user_id')

    cursor = mysql.connection.cursor()

    # =========================
    # ⭐ SUPERADMIN LOGIC ⭐
    # =========================
    if user_role == 'superadmin':
        cursor.execute("SELECT role, COUNT(*) AS count FROM users WHERE is_active = TRUE GROUP BY role")
        role_counts_raw = cursor.fetchall()
        user_role_counts = {row['role']: row['count'] for row in role_counts_raw}

        cursor.execute("SELECT department, COUNT(*) AS count FROM users WHERE is_active = TRUE GROUP BY department")
        dept_counts_raw = cursor.fetchall()
        user_dept_counts = {row['department']: row['count'] for row in dept_counts_raw}

        cursor.execute("""
            SELECT u.*, h.name AS assigned_head_name
            FROM users u LEFT JOIN users h ON u.assigned_head = h.id
            WHERE u.is_active = TRUE
        """)
        all_users = cursor.fetchall()

    # =========================
    # ⭐ ADMIN LOGIC ⭐
    # =========================
    elif user_role == 'head':

    

    # =========================
    # Role counts (ONLY their team)
    # =========================
        cursor.execute("""
            SELECT role, COUNT(*) AS count
            FROM users
            WHERE is_active = TRUE
            AND assigned_head = %s
            AND role IN ('employee', 'intern')
            GROUP BY role
        """, (head_id,))
        role_counts_raw = cursor.fetchall()
        user_role_counts = {row['role']: row['count'] for row in role_counts_raw}

        # =========================
        # Department count (derived from team)
        # =========================
        user_dept_counts = {
            session.get('department'): sum(user_role_counts.values())
        }

        # =========================
        # Fetch ONLY users assigned to this head
        # =========================
        cursor.execute("""
            SELECT u.*, h.name AS assigned_head_name
            FROM users u
            LEFT JOIN users h ON u.assigned_head = h.id
            WHERE u.is_active = TRUE
            AND u.assigned_head = %s
            AND u.role IN ('employee', 'intern')
        """, (head_id,))
        all_users = cursor.fetchall()


    else:
        flash("Unauthorized access.", "danger")
        return redirect(url_for("login"))

    cursor.close()

    return render_template(
        'superadmin/systemaccess.html',
        user_role_counts=user_role_counts,
        user_dept_counts=user_dept_counts,
        all_users=all_users,
        user_role=user_role
    )




@app.route('/delete_user/<int:user_id>', methods=['POST'])
def delete_user(user_id):
    if 'logged_in' not in session or session.get('user_role') != 'superadmin':
        return jsonify({"success": False, "message": "Unauthorized"}), 403

    cursor = mysql.connection.cursor()

    try:
        # 1️⃣ Remove user from all project_members
        cursor.execute("""
            UPDATE project_members
            SET user_ids = TRIM(BOTH ',' FROM
                REPLACE(
                    REPLACE(
                        REPLACE(user_ids, CONCAT(',', %s, ','), ','),
                    CONCAT(%s, ','), ''),
                CONCAT(',', %s), '')
            )
            WHERE FIND_IN_SET(%s, user_ids)
        """, (user_id, user_id, user_id, user_id))

        # 2️⃣ Soft delete user
        cursor.execute(
            "UPDATE users SET is_active = FALSE WHERE id = %s",
            (user_id,)
        )

        mysql.connection.commit()
        return jsonify({"success": True, "message": "User deactivated and removed from projects."})

    except Exception as e:
        mysql.connection.rollback()
        print("Deactivate user error:", e)
        return jsonify({"success": False, "message": "Database error."}), 500

    finally:
        cursor.close()


@app.route('/get_department_head/<department_name>')
def get_department_head(department_name):
    if 'logged_in' not in session or session.get('user_role') != 'superadmin':
        return jsonify({"success": False, "message": "Unauthorized"}), 403

    cursor = mysql.connection.cursor()
    try:
        cursor.execute("""
            SELECT name FROM users WHERE department = %s AND role IN ('head', 'superadmin') AND is_active = TRUE LIMIT 1
        """, (department_name,))
        head = cursor.fetchone()
        return jsonify({"success": True, "head_name": head['name']}) if head else jsonify({"success": False, "message": "No head found."})
    except Exception as e:
        return jsonify({"success": False, "message": "Database error."}), 500
    finally:
        cursor.close()
@app.route('/update_user_details', methods=['POST'])
def update_user_details():
    if 'logged_in' not in session:
        return jsonify({"success": False, "message": "Unauthorized"}), 403

    data = request.get_json()
    cursor = mysql.connection.cursor()

    try:
        cursor.execute("""
            UPDATE users
            SET name = %s,
                email = %s,
                role = %s
            WHERE id = %s
        """, (
            data['name'],
            data['email'],
            data['role'],
            data['user_id']
        ))

        mysql.connection.commit()
        return jsonify({"success": True})

    except Exception as e:
        mysql.connection.rollback()
        print("UPDATE ERROR:", e)
        return jsonify({"success": False, "message": "Update failed"}), 500

    finally:
        cursor.close()



#get user summary to show when deleting
@app.route('/user_dependency_summary/<user_id>')
def user_dependency_summary(user_id):
    if 'logged_in' not in session or session.get('user_role') != 'superadmin':
        return jsonify({"success": False, "message": "Unauthorized"}), 403

    data = get_employee_data(user_id)

    if not data or not data.get("current_user"):
        return jsonify({"success": False, "message": "User not found"}), 404

    name = data["current_user"]["name"]

    # Count ongoing (not completed) projects
    ongoing_projects = sum(
        1 for p in data["projects"]
        if p["status"] != "Completed"
    )

    # Pending tasks already filtered in helper
    pending_tasks = len(data["assigned_tasks"])

    return jsonify({
        "success": True,
        "name": name,
        "projects": ongoing_projects,
        "tasks": pending_tasks
    })



@app.route('/update_password', methods=['POST'])
def update_password():
    if 'logged_in' not in session:
        flash("Please log in.", "danger")
        return redirect(url_for('login'))

    user_id = session['user_id']
    current_password = request.form.get('current_password')
    new_password = request.form.get('new_password')
    confirm_new_password = request.form.get('confirm_new_password')

    cursor = mysql.connection.cursor()
    cursor.execute("SELECT password FROM users WHERE id = %s", (user_id,))
    user_data = cursor.fetchone()
    cursor.close()

    if not user_data or current_password != user_data['password']:
        flash("❌ Current password incorrect.", "danger")
        if session['user_role'] == 'head':
            return redirect(url_for('admin_profile'))
        return redirect(url_for('superadmin_profile'))

    if new_password != confirm_new_password:
        flash("❌ New passwords do not match.", "danger")
        if session['user_role'] == 'head':
            return redirect(url_for('admin_profile'))
        return redirect(url_for('superadmin_profile'))

    if len(new_password) < 6:
        flash("❌ New password must be at least 6 characters.", "danger")
        if session['user_role'] == 'head':
            return redirect(url_for('admin_profile'))
        return redirect(url_for('superadmin_profile'))

    try:
        cursor = mysql.connection.cursor()
        cursor.execute(
            "UPDATE users SET password = %s WHERE id = %s", (new_password, user_id))
        mysql.connection.commit()
        flash("✅ Password updated successfully!", "success")
    except Exception as e:
        mysql.connection.rollback()
        flash(f"❌ Error updating password: {e}", "danger")
    finally:
        cursor.close()

    if session['user_role'] == 'head':
        return redirect(url_for('admin_profile'))
    return redirect(url_for('superadmin_profile'))


@app.route('/superadmin/profile')
def superadmin_profile():
    if 'logged_in' not in session or session['user_role'] != 'superadmin':
        flash('Unauthorized access.', 'danger')
        return redirect(url_for('login'))

    user_id = session.get('user_id')
    cur = mysql.connection.cursor()
    try:
        cur.execute(
            "SELECT id, name, email, role, department, assigned_head FROM users WHERE id = %s", (user_id,))
        user_data = cur.fetchone()
    except Exception as e:
        flash(f"Error fetching profile data: {e}", "danger")
        user_data = {}
    finally:
        cur.close()

    # Corrected Path
    return render_template('superadmin/profile.html', user=user_data)


@app.route('/get_user_name/<int:user_id>')
def get_user_name(user_id):
    if 'logged_in' not in session:
        return jsonify({"success": False, "message": "Unauthorized"}), 403

    try:
        cursor = mysql.connection.cursor()
        cursor.execute("SELECT name FROM users WHERE id = %s", (user_id,))
        user = cursor.fetchone()
        return jsonify({"success": True, "user_name": user['name']}) if user else jsonify({"success": False, "message": "User not found"}), 404
    except Exception as e:
        return jsonify({"success": False, "message": "Database error"}), 500
    finally:
        cursor.close()


# ==============================================================================
# == ADMIN ROUTES
# ==============================================================================

def get_head_project_list(cur, head_id):
    query = """
        SELECT 
            p.id,
            p.name AS project_name,
            p.department,
            p.status,
            p.created_at,
            p.deadline_date,
            GROUP_CONCAT(u.name SEPARATOR ', ') AS members
        FROM projects p
        LEFT JOIN project_members pm 
            ON pm.project_id = p.id
        LEFT JOIN users u 
            ON FIND_IN_SET(u.id, pm.user_ids)
        WHERE p.project_head_id = %s
        GROUP BY p.id
        ORDER BY p.deadline_date ASC
    """
    cur.execute(query, (head_id,))
    return cur.fetchall()



@app.route('/admin/dashboard')
@admin_required
def admin_dashboard():

    department = session.get('department').capitalize()
    user_role = session.get('user_role')
    today=date.today()
    due_soon_limit = today + timedelta(days=5)
    head_id = session['user_id']
    cur = mysql.connection.cursor(MySQLdb.cursors.DictCursor)

    # dept_projects = get_dept_projects(
    #     cur,
    #     department,
    #     ['Ongoing', 'Pending', 'Completed']
    # )
    
    project_list = get_head_project_list(cur, head_id)

    project_summary = get_head_projects(
    cur,
    head_id,
    ['Ongoing', 'Onhold', 'Completed']
)

    # dept_tasks = get_dept_tasks(cur, department)

    dept_tasks = get_head_tasks(cur, head_id)

    cur.execute("""
        SELECT COUNT(DISTINCT tw.user_id) AS count
        FROM today_work tw
        JOIN users u ON tw.user_id = u.id
        WHERE tw.work_date = %s
        AND (u.id = %s OR u.assigned_head = %s)
    """, (today, head_id, head_id))

    today_work_count = cur.fetchone()['count']

    cur.close()

    # Normalize for template
    sales_projects = []
    developer_projects = []
    graphics_projects = []
    digital_projects = []

    # if department == 'Sales':
    #     sales_projects = project_summary  
    # elif department == 'Developer':
    #     developer_projects = project_summary  
    # elif department == 'Graphics':
    #     graphics_projects = project_summary  
    # elif department == 'Digital':
    #     digital_projects = project_summary  

    for p in project_list:
        if p['department'] == 'Sales':
            sales_projects.append(p)
        elif p['department'] == 'Developer':
            developer_projects.append(p)
        elif p['department'] == 'Graphics':
            graphics_projects.append(p)
        elif p['department'] == 'Digital':
            digital_projects.append(p)

    return render_template(
        'superadmin/dashboard.html',
        user_role=user_role,
        department=department.lower(),
        dept_tasks=dept_tasks,
        sales_projects=sales_projects,
        developer_projects=developer_projects,
        graphics_projects=graphics_projects,
        digital_projects=digital_projects,
        today=today,
        due_soon_limit=due_soon_limit,
        today_work_count=today_work_count,
        projects=project_list,          # 👈 REAL list for counts
        project_summary=project_summary  # optional, keeps counts working
    )





    

  

# admin total ongoing projects list

@app.route('/admin/admin_ongoing_projects')
@admin_required
def admin_ongoing_projects():

    department = session.get('department')
    cur = mysql.connection.cursor()

    cur.execute("""
        SELECT 
            p.id AS project_id,
            p.name AS project_name,
            p.created_at,
            p.deadline_date,
            p.status,

            (
                SELECT GROUP_CONCAT(name SEPARATOR ', ')
                FROM users 
                WHERE FIND_IN_SET(id, (
                    SELECT user_ids FROM project_members WHERE project_id = p.id LIMIT 1
                ))
            ) AS members

        FROM projects p
        WHERE p.department = %s
          AND p.status IN ('Ongoing', 'In Progress', 'Onhold')
        ORDER BY p.deadline_date ASC
    """, (department,))

    projects = cur.fetchall()
    cur.close()

    return render_template(
        'superadmin/ongoing_projects.html',
        projects=projects,
        department=department,
        user_role='head'
    )



@app.route('/admin/submitted_tasks')
@admin_required
def admin_submitted_tasks():
    department = session.get('department')
    project_id_filter = request.args.get('project_id', type=int)

    cur = mysql.connection.cursor()
    try:
        # Base query
        query = """
            SELECT
                st.id,
                st.task_description,
                st.status,
                st.attachment,
                st.ai_summary,
                st.created_at AS submitted_date,
                u.name AS employee_name,
                at.task_name,
                p.name AS project_name
            FROM submitted_tasks st
            JOIN users u ON st.user_id = u.id
            LEFT JOIN assigned_tasks at ON st.assigned_task_id = at.id
            LEFT JOIN projects p ON st.project_id = p.id
            WHERE u.department = %s
        """

        params = [department]

        # Add project filter if provided
        if project_id_filter:
            query += " AND p.id = %s"
            params.append(project_id_filter)

        query += " ORDER BY st.created_at DESC"

        cur.execute(query, tuple(params))
        submitted_tasks = cur.fetchall()

        for task in submitted_tasks:
            if task.get('submitted_date') and isinstance(task['submitted_date'], (datetime.date, datetime.datetime)):
                task['submitted_date'] = task['submitted_date'].strftime(
                    '%d/%m/%Y')

        # Fetch projects for the filter dropdown (only from the admin's department)
        cur.execute(
            "SELECT id, name FROM projects WHERE department = %s ORDER BY name", (department,))
        projects = cur.fetchall()

    except Exception as e:
        flash(f"Error fetching submitted tasks: {e}", "danger")
        submitted_tasks = []
        projects = []
    finally:
        cur.close()

    return render_template('superadmin/submitted_tasks.html',
                           submitted_tasks=submitted_tasks,
                           projects=projects,
                           selected_project=project_id_filter)


@app.route('/admin/adduser')
@admin_required
def admin_adduser():
    department = session.get('department')
    cursor = mysql.connection.cursor()
    cursor.execute(
        "SELECT id, name, email, role, department FROM users WHERE is_active = TRUE AND role = 'head' AND department = %s", (department,))
    all_potential_heads = cursor.fetchall()
    cursor.close()
    # Corrected Path
    return render_template('superadmin/adduser.html', all_users=all_potential_heads, department=department)


@app.route('/admin/createproject')
@admin_required
def admin_createproject():
    department = session.get('department')
    head_id = session.get('user_id')

    cursor = mysql.connection.cursor()

    # Heads in same department (optional, keep if needed)
    cursor.execute(
        "SELECT id, name, email, department "
        "FROM users "
        "WHERE role = 'head' AND department = %s AND is_active = TRUE",
        (department,)
    )
    admins = cursor.fetchall()

    # ✅ ONLY members assigned to THIS head
    cursor.execute(
        "SELECT id, name, role, department, assigned_head "
        "FROM users "
        "WHERE role IN ('employee','intern') "
        "AND assigned_head = %s "
        "AND is_active = TRUE",
        (head_id,)
    )
    members = cursor.fetchall()

    busy_members = get_busy_user_ids(cursor)
    cursor.close()

    today_date = datetime.date.today().strftime('%Y-%m-%d')

    return render_template(
        'superadmin/createproject.html',
        admins=admins,
        today_date=today_date,
        department=department,
        members=members,
        busy_members=busy_members
    )



@app.route('/admin/viewprojects')
@admin_required
def admin_viewprojects():
    department = session.get('department')
    head_id = session.get('user_id')

    page = request.args.get('page', 1, type=int)
    per_page = 10
    status_filter = request.args.get('status')  # <-- 🟢 ADDED STATUS FILTER

    cur = mysql.connection.cursor()
    try:
        # 🟢 Count projects with filter
        if status_filter:
            cur.execute("""
                SELECT COUNT(id) AS count
                FROM projects
                WHERE project_head_id = %s
                AND status = %s
            """, (head_id, status_filter))
        else:
            cur.execute("""
                SELECT COUNT(id) AS count
                FROM projects
                WHERE project_head_id = %s
            """, (head_id,))

        total_projects = cur.fetchone()['count']
        total_pages = ceil(total_projects / per_page)
        offset = (page - 1) * per_page

        # 🟢 Fetch filtered OR unfiltered list
        if status_filter:
            cur.execute("""
                SELECT p.id, p.name, p.description, p.department, p.created_at,
                    p.deadline_date, p.status, p.project_head_id,
                    u.name AS project_head_name
                FROM projects p
                LEFT JOIN users u ON p.project_head_id = u.id
                WHERE p.project_head_id = %s
                AND p.status = %s
                ORDER BY p.created_at DESC
                LIMIT %s OFFSET %s
            """, (head_id, status_filter, per_page, offset))
        else:
            cur.execute("""
                SELECT p.id, p.name, p.description, p.department, p.created_at,
                    p.deadline_date, p.status, p.project_head_id,
                    u.name AS project_head_name
                FROM projects p
                LEFT JOIN users u ON p.project_head_id = u.id
                WHERE p.project_head_id = %s
                ORDER BY p.created_at DESC
                LIMIT %s OFFSET %s
            """, (head_id, per_page, offset))


        projects = cur.fetchall()

        # Get admins of same department only
        cur.execute("""
            SELECT id, name, email, department 
            FROM users 
            WHERE role = 'head' AND department = %s AND is_active = TRUE
        """, (department,))
        admins = cur.fetchall()

        cur.execute("SELECT id, name, role, department FROM users WHERE role IN ('employee','intern') AND is_active=TRUE")
        members = cur.fetchall()
        busy_members = get_busy_user_ids(cur)

        # Format dates
        for project in projects:
            project['status'] = project['status'] or 'N/A'
            created_at_date = project.get('created_at')
            deadline_date_date = project.get('deadline_date')

            project['created_at'] = created_at_date.strftime('%d/%m/%Y') if isinstance(created_at_date, datetime.date) else 'N/A'
            project['deadline_date'] = deadline_date_date.strftime('%d/%m/%Y') if isinstance(deadline_date_date, datetime.date) else 'N/A'
            project['created_at_raw'] = created_at_date.strftime('%Y-%m-%d') if created_at_date else ''
            project['deadline_date_raw'] = deadline_date_date.strftime('%Y-%m-%d') if deadline_date_date else ''

            # Fetch project members
            cur.execute("SELECT user_ids FROM project_members WHERE project_id = %s", (project["id"],))
            row = cur.fetchone()

            project["member_ids_list"] = row["user_ids"].split(",") if row and row["user_ids"] else []

    except Exception as e:
        flash(f"Error fetching projects: {e}", "danger")
        projects, admins, total_pages = [], [], 1
    finally:
        cur.close()

    return render_template('superadmin/viewproject.html',
                           projects=projects,
                           admins=admins,
                           members=members,
                           busy_members=busy_members,
                           current_page=page,
                           total_pages=total_pages,
                           endpoint='admin_viewprojects',
                           selected_status=status_filter)  # <-- 🟢 Pass to template


@app.route('/admin/createtask')
@admin_required
def admin_createtask():
    department = session.get('department')
    head_id = session.get('user_id')

    cur = mysql.connection.cursor()
    try:
        cur.execute(
            "SELECT id, name, email, department, assigned_head FROM users "
            "WHERE role IN ('employee', 'intern') "
            "AND assigned_head = %s "
            "AND is_active = TRUE "
            "ORDER BY name",
            (head_id,)
        )

        employees_and_interns = cur.fetchall()
        cur.execute(
            "SELECT id, name, department,status FROM projects WHERE project_head_id = %s ORDER BY name", (head_id,))
        projects = cur.fetchall()
         # get busy users using assigned_tasks logic
        busy_users = get_busy_user_ids(cur)
    except Exception as e:
        flash(f"Error fetching data: {e}", "danger")
        employees_and_interns, projects = [], []
        busy_users = set()
    finally:
        
        cur.close()

    today_date = datetime.date.today().strftime('%Y-%m-%d')
    

    # Corrected Path
    return render_template('superadmin/createtask.html', employees_and_interns=employees_and_interns, projects=projects, today_date=today_date,busy_users=busy_users)


# admin submit task section

@app.route('/admin/admin_submit_task', methods=['GET', 'POST'])
def admin_submit_task():
    if 'logged_in' not in session or session.get('user_role') != 'head':
        flash('Unauthorized access.', 'danger')
        return redirect(url_for('login'))

    user_id = session['user_id']

    if request.method == 'POST':
        project_id = request.form.get('project_id') 

        task_description = request.form.get('task_description')
        status = request.form.get('status')
        assigned_task_id = request.form.get('assigned_task_id')
        attachment = request.files.get('attachment')

        filename = None
        if attachment and allowed_file(attachment.filename):
            filename = secure_filename(attachment.filename)
            attachment.save(os.path.join(app.config['UPLOAD_FOLDER'], filename))

        ai_summary = summarize_with_openrouter(task_description)

        try:
            cursor = mysql.connection.cursor()
            cursor.execute("""
                INSERT INTO submitted_tasks 
                (user_id, task_description, status, attachment, ai_summary, assigned_task_id,project_id, created_at)
                VALUES (%s, %s, %s, %s, %s, %s,%s,NOW())
            """, (user_id, task_description, status, filename, ai_summary,
                  assigned_task_id if assigned_task_id else None,
                  project_id if project_id else None))

            # Update original assigned task status if linked
            if assigned_task_id:
                cursor.execute("""
                    UPDATE assigned_tasks
                    SET status = %s
                    WHERE id = %s AND assigned_to_user_id = %s
                """, (status, assigned_task_id, user_id))

            # get admin’s head/superadmin email
            cursor.execute("""
                SELECT u.name as admin_name, u.email as admin_email,
                       h.name as head_name, h.email as head_email
                FROM users u
                LEFT JOIN users h ON u.assigned_head = h.id
                WHERE u.id = %s
            """, (user_id,))
            email_data = cursor.fetchone()

            mysql.connection.commit()
            flash('Task submitted successfully!', 'success')

            # send email
            if email_data and email_data['head_email']:
                subject = f"Task Update from {email_data['admin_name']}"
                body = f"""
                <html>
                <body>
                    <p>Hello {email_data['head_name']},</p>
                    <p><b>{email_data['admin_name']}</b> has submitted a task update.</p>
                    <p><b>Update:</b><br>{task_description}</p>
                    <hr>
                    <p><b>AI Summary:</b><br>{ai_summary}</p>
                </body>
                </html>
                """

                send_email(
                    recipient_email=email_data['head_email'],
                    subject=subject,
                    body=body,
                    reply_to_email=email_data['admin_email']
                )

        except Exception as e:
            mysql.connection.rollback()
            flash(f"Error submitting task: {e}", "danger")
        finally:
            cursor.close()

        return redirect(url_for('admin_submit_task'))

    # fetch tasks assigned to this admin
    cursor = mysql.connection.cursor()
    cursor.execute("""
            SELECT 
            at.id, 
            at.task_name, 
            at.project_id, 
            p.name as project_name,
            
            at.due_date
        FROM assigned_tasks at
        LEFT JOIN projects p ON at.project_id = p.id
        WHERE at.assigned_to_user_id = %s

    """, (user_id,))
    assigned_tasks = cursor.fetchall()
    cursor.execute("""
    SELECT id, name , status
    FROM projects
    WHERE project_head_id = %s
""", (user_id,))
    projects = cursor.fetchall()

    cursor.close()

    return render_template('superadmin/submit_task.html', assigned_tasks=assigned_tasks,projects=projects)

# admin viewTask section
@app.route('/admin/viewtasks')
@admin_required
def admin_viewtasks():
    head_id = session.get('user_id')
    page = request.args.get('page', 1, type=int)
    per_page = 15

    if page < 1:
        page = 1

    cur = mysql.connection.cursor()
    try:
        # COUNT (head-wise)
        cur.execute("""
            SELECT COUNT(at.id) AS count
            FROM assigned_tasks at
            JOIN projects p ON at.project_id = p.id
            WHERE p.project_head_id = %s
        """, (head_id,))
        total_tasks = cur.fetchone()['count']
        total_pages = ceil(total_tasks / per_page) if total_tasks else 1

        offset = (page - 1) * per_page

        # FETCH TASKS (head-wise)
        cur.execute("""
            SELECT at.id, at.task_name, at.task_description, at.due_date, at.status,
                   at.project_id, at.department, at.date_assigned, at.time_assigned,
                   at.assigned_to_user_id,
                   u.name AS assigned_to_name, u.role,
                   p.name AS project
            FROM assigned_tasks at
            JOIN projects p ON at.project_id = p.id
            LEFT JOIN users u ON at.assigned_to_user_id = u.id
            WHERE p.project_head_id = %s
            ORDER BY at.date_assigned DESC, at.time_assigned DESC
            LIMIT %s OFFSET %s
        """, (head_id, per_page, offset))
        tasks = cur.fetchall()

        # Project list (head-wise)
        cur.execute("""
            SELECT id, name
            FROM projects
            WHERE project_head_id = %s
            ORDER BY name
        """, (head_id,))
        projects = cur.fetchall()

        # Formatting
        for task in tasks:
            task['assigned_date_formatted'] = (
                task['date_assigned'].strftime('%d/%m/%Y')
                if task.get('date_assigned') else 'N/A'
            )
            task['time_assigned_formatted'] = (
                str(task['time_assigned'])
                if task.get('time_assigned') else 'N/A'
            )
            if task.get('due_date'):
                task['due_date_iso'] = task['due_date'].strftime('%Y-%m-%d')   # for input
                task['due_date_display'] = task['due_date'].strftime('%d/%m/%Y')  # for humans
            else:
                task['due_date_iso'] = ''
                task['due_date_display'] = ''

    except Exception as e:
        flash(f"Error fetching tasks: {e}", "danger")
        tasks, projects, total_pages = [], [], 1
    finally:
        cur.close()

    return render_template(
        'superadmin/viewtasks.html',
        tasks=tasks,
        projects=projects,
        current_page=page,
        total_pages=total_pages,
        endpoint='admin_viewtasks'
    )



@app.route('/admin/profile')
@admin_required
def admin_profile():
    user_id = session.get('user_id')
    cur = mysql.connection.cursor()
    try:
        cur.execute(
            "SELECT id, name, email, role, department, assigned_head FROM users WHERE id = %s", (user_id,))
        user_data = cur.fetchone()
    except Exception as e:
        flash(f"Error fetching profile data: {e}", "danger")
        user_data = {}
    finally:
        cur.close()

    # Corrected Path
    return render_template('superadmin/profile.html', user=user_data)

# ==============================================================================
# == EMPLOYEE & INTERN ROUTES
# ==============================================================================

def get_employee_data(user_id):
    cursor = mysql.connection.cursor()

    # Current user
    cursor.execute("""
        SELECT u.*, h.name AS assigned_head_name, h.email AS head_email
        FROM users u
        LEFT JOIN users h ON u.assigned_head = h.id
        WHERE u.id = %s
    """, (user_id,))
    current_user_data = cursor.fetchone()

    # Assigned tasks (Pending)
    cursor.execute("""
        SELECT at.*, p.name AS project_name, u.name AS assigned_by_name,
               at.due_date due_date,
               at.date_assigned as assigned_date
        FROM assigned_tasks at
        LEFT JOIN projects p ON at.project_id = p.id
        LEFT JOIN users u ON at.assigned_by_user_id = u.id
        WHERE at.status != 'Completed'
          AND at.assigned_to_user_id = %s
        ORDER BY at.due_date DESC
    """, (user_id,))
    assigned_tasks = cursor.fetchall()

    # Submitted tasks (Completed)
    cursor.execute("""
        SELECT st.*, p.name AS project_name,
               at.date_assigned, at.due_date, at.task_name,
               st.created_at AS completed_date
        FROM submitted_tasks st
        LEFT JOIN assigned_tasks at ON st.assigned_task_id = at.id
        LEFT JOIN projects p ON st.project_id = p.id
        WHERE st.status = 'Completed'
          AND st.user_id = %s
        ORDER BY st.created_at DESC
    """, (user_id,))
    submitted_tasks = cursor.fetchall()

    # Employee projects
    cursor.execute("""
        SELECT 
            p.id,
            p.name AS project_name,
            p.deadline_date,
            p.description,
            p.created_at,
            p.status
        FROM projects p
        JOIN project_members pm ON pm.project_id = p.id
        WHERE FIND_IN_SET(%s, pm.user_ids)
        ORDER BY 
            CASE 
                WHEN p.status = 'Completed' THEN 2
                ELSE 1
            END,
            p.deadline_date ASC
    """, (user_id,))
    rows = cursor.fetchall()

    projects = [{
        'id': row['id'],
        'project_name': row['project_name'],
        'deadline_date': row['deadline_date'],
        'description': row['description'],
        'created_at': row['created_at'],
        'status': row['status']
    } for row in rows]

    cursor.close()

    return {
        "current_user": current_user_data,
        "assigned_tasks": assigned_tasks,
        "submitted_tasks": submitted_tasks,
        "projects": projects,
        "today": date.today()
    }

@app.route('/employee/dashboard')
def employee_dashboard():
    if 'user_id' not in session:
        return redirect(url_for('login'))
    
    data = get_employee_data(session['user_id'])

    return render_template(
        "employee/Edashboard.html",
        current_user=data['current_user'],
        assigned_tasks=data['assigned_tasks'],
        submitted_tasks=data['submitted_tasks'],
        projects=data['projects'],
        today=data['today']
    )

#view project section
@app.route('/employee/ongoing-projects')
def employee_ongoing_projects():
    if 'logged_in' not in session or session.get('user_role') not in ['employee', 'intern']:
        flash('Unauthorized access.', 'danger')
        return redirect(url_for('login'))

    user_id = session.get('user_id')
    cur = mysql.connection.cursor()

    cur.execute("""
        SELECT 
            p.id AS project_id,
            p.name AS project_name,
            p.created_at,
            p.deadline_date,
            p.status,

            (
                SELECT GROUP_CONCAT(name SEPARATOR ', ')
                FROM users 
                WHERE FIND_IN_SET(id, (
                    SELECT user_ids FROM project_members WHERE project_id = p.id LIMIT 1
                ))
            ) AS members

        FROM projects p
        WHERE p.status IN ('Ongoing', 'In Progress', 'Onhold')
          AND EXISTS (
                SELECT 1 FROM project_members pm
                WHERE pm.project_id = p.id
                  AND FIND_IN_SET(%s, pm.user_ids)
          )
        ORDER BY p.deadline_date ASC
    """, (user_id,))

    projects = cur.fetchall()
    cur.close()

    return render_template('superadmin/ongoing_projects.html', projects=projects, user_role='employee', today=date.today())

#employee create task section
@app.route('/employee/createtask')
def employee_createtask():
    if 'logged_in' not in session or session.get('user_role') != 'employee':
        flash('Unauthorized access. Please log in.', 'danger')
        return redirect(url_for('login'))

    cur = mysql.connection.cursor(DictCursor)

    try:
        cur.execute("""SELECT id, name, email, department FROM users WHERE role = 'intern' AND is_active = TRUE""")
        interns = cur.fetchall()

        cur.execute("""SELECT DISTINCT a.id, a.name, a.department FROM projects a INNER JOIN project_members b ON a.id = b.project_id WHERE FIND_IN_SET(%s, b.user_ids) ORDER BY a.name
        """, (session['user_id'],))
        projects = cur.fetchall()

        busy_users = get_busy_user_ids(cur)

    except Exception as e:
        flash("Error fetching data.", "danger")
        print(e)
        interns = []
        projects = []
        busy_users = set()

    finally:
        cur.close()

    today_date = datetime.date.today().strftime('%Y-%m-%d')

    return render_template('superadmin/createtask.html',interns=interns, projects=projects, today_date=today_date, busy_users=busy_users)

@app.route('/employee/submit_task', methods=['GET', 'POST'])
def employee_submit_task():
    if 'logged_in' not in session or session.get('user_role') not in ['employee', 'intern']:
        flash('Unauthorized access.', 'danger')
        return redirect(url_for('login'))

    user_id = session['user_id']

    if request.method == 'POST':
        project_id = request.form.get('project_id') or request.form.get('project_dropdown')

        task_description = request.form.get('task_description')
        status = request.form.get('status')
        assigned_task_id = request.form.get('assigned_task_id')
        attachment = request.files.get('attachment')

        filename = None
        if attachment and allowed_file(attachment.filename):
            filename = secure_filename(attachment.filename)
            attachment.save(os.path.join(app.config['UPLOAD_FOLDER'], filename))

        ai_summary = summarize_with_openrouter(task_description)

        try:
            cursor = mysql.connection.cursor()
            cursor.execute("""
                INSERT INTO submitted_tasks 
                (user_id, task_description, status, attachment, ai_summary, assigned_task_id,project_id, created_at)
                VALUES (%s, %s, %s, %s, %s, %s,%s,NOW())
            """, (user_id, task_description, status, filename, ai_summary,
                  assigned_task_id if assigned_task_id else None,
                  project_id if project_id else None))

            # Update original assigned task status if linked
            if assigned_task_id:
                cursor.execute("""
                    UPDATE assigned_tasks
                    SET status = %s
                    WHERE id = %s AND assigned_to_user_id = %s
                """, (status, assigned_task_id, user_id))

            # get admin’s head/superadmin email
            cursor.execute("""
                SELECT u.name as admin_name, u.email as admin_email,
                       h.name as head_name, h.email as head_email
                FROM users u
                LEFT JOIN users h ON u.assigned_head = h.id
                WHERE u.id = %s
            """, (user_id,))
            email_data = cursor.fetchone()

            mysql.connection.commit()
            flash('Task submitted successfully!', 'success')

            # send email
            if email_data and email_data['head_email']:
                subject = f"Task Update from {email_data['admin_name']}"
                body = f"""
                <html>
                <body>
                    <p>Hello {email_data['head_name']},</p>
                    <p><b>{email_data['admin_name']}</b> has submitted a task update.</p>
                    <p><b>Update:</b><br>{task_description}</p>
                    <hr>
                    <p><b>AI Summary:</b><br>{ai_summary}</p>
                </body>
                </html>
                """

                send_email(
                    recipient_email=email_data['head_email'],
                    subject=subject,
                    body=body,
                    reply_to_email=email_data['admin_email']
                )

        except Exception as e:
            mysql.connection.rollback()
            flash(f"Error submitting task: {e}", "danger")
        finally:
            cursor.close()

        return redirect(url_for('employee_submit_task'))

    # fetch tasks assigned to this employee
    cursor = mysql.connection.cursor()
    cursor.execute("""
            SELECT 
            at.id, 
            at.task_name, 
            at.project_id, 
            p.name as project_name,
            at.due_date, 
            at.date_assigned
        FROM assigned_tasks at
        LEFT JOIN projects p ON at.project_id = p.id
        WHERE at.assigned_to_user_id = %s And at.status !='Completed'

    """, (user_id,))
    assigned_tasks = cursor.fetchall()
    cursor.execute("""
        SELECT DISTINCT p.id, p.name
        FROM projects p
        LEFT JOIN assigned_tasks at ON at.project_id = p.id
        WHERE at.assigned_to_user_id = %s
    """, (user_id,))
    projects = cursor.fetchall()

    cursor.close()

    return render_template('superadmin/submit_task.html', assigned_tasks=assigned_tasks,projects=projects)


@app.route('/employee/assigned_tasks')
def employee_assigned_tasks():
    if 'logged_in' not in session or session.get('user_role') not in ['employee', 'intern']:
        flash('Unauthorized access.', 'danger')
        return redirect(url_for('login'))

    user_id = session['user_id']
    data = get_employee_data(user_id)
    assigned_tasks = data["assigned_tasks"]

    # Corrected Path
    return render_template('employee/taskassign.html', name=session['username'], assigned_tasks=assigned_tasks)


@app.route('/employee/submitted_tasks')
def employee_submitted_tasks():
    if 'logged_in' not in session or session.get('user_role') not in ['employee', 'intern']:
        flash('Unauthorized access.', 'danger')
        return redirect(url_for('login'))

    user_id = session['user_id']
    page = request.args.get('page', 1, type=int)
    per_page = 5  # Items per page

    cursor = mysql.connection.cursor()
    try:
        # Get total count of submitted tasks for the user
        cursor.execute(
            "SELECT COUNT(id) as count FROM submitted_tasks WHERE user_id = %s", (user_id,))
        total_tasks = cursor.fetchone()['count']
        total_pages = ceil(total_tasks / per_page)
        offset = (page - 1) * per_page

        # Fetch paginated submitted tasks
        cursor.execute("""
            SELECT st.*, p.name as project_name, at.due_date as due_date,
                   DATE_FORMAT(st.created_at, '%%d/%%m/%%Y') as created_at_date
            FROM submitted_tasks st
            LEFT JOIN assigned_tasks at ON st.assigned_task_id = at.id
            LEFT JOIN projects p ON st.project_id = p.id
            WHERE st.user_id = %s
            ORDER BY st.created_at DESC
            LIMIT %s OFFSET %s
        """, (user_id, per_page, offset))
        submitted_tasks = cursor.fetchall()
    except Exception as e:
        flash(f"Error fetching submitted tasks: {e}", "danger")
        submitted_tasks = []
        total_pages = 1
    finally:
        cursor.close()

    return render_template(
        'employee/submittedtask.html',
        name=session['username'],
        submitted_tasks=submitted_tasks,
        current_page=page,
        total_pages=total_pages,
        endpoint='employee_submitted_tasks'
    )


@app.route('/employee/profile')
def employee_profile():
    if 'logged_in' not in session or session.get('user_role') not in ['employee', 'intern']:
        flash('Unauthorized access.', 'danger')
        return redirect(url_for('login'))

    
    user_id = session.get('user_id')
    cur = mysql.connection.cursor()
    try:
        cur.execute(
            "SELECT id, name, email, role, department, assigned_head FROM users WHERE id = %s", (user_id,))
        user_data = cur.fetchone()
    except Exception as e:
        flash(f"Error fetching profile data: {e}", "danger")
        user_data = {}
    finally:
        cur.close()

    # Corrected Path
    return render_template('superadmin/profile.html', user=user_data)

@app.route('/employee/workreport')
def employee_workreport():
    if 'logged_in' not in session or session.get('user_role') not in ['employee', 'intern']:
        flash('Unauthorized access.', 'danger')
        return redirect(url_for('login'))
    #input sanitization
    def clean_str(val):
        if not val or not isinstance(val, str):
            return None

        val = val.strip()

        if val.lower() in ('none', 'null', ''):
            return None

        return val


    def clean_int(val):
        try:
            return int(val)
        except (TypeError, ValueError):
            return None

    user_id = session['user_id']
    # page = request.args.get('page', 1, type=int)
    # per_page = 10  # Items per page

    #Get projects to show project summary
    selected_project = clean_int(request.args.get('project_id'))

    # Get filter dates
    from_date = clean_str(request.args.get('from_date'))
    to_date = clean_str(request.args.get('to_date'))

    # # Build date filter query
    # date_filter = ""
    # params = [user_id]  # Always include user_id as first param
    # if from_date and to_date:
    #     date_filter = "AND DATE(st.created_at) BETWEEN %s AND %s"
    #     params.extend([from_date, to_date])
    # elif from_date:
    #     date_filter = "AND DATE(st.created_at) >= %s"
    #     params.append(from_date)
    # elif to_date:
    #     date_filter = "AND DATE(st.created_at) <= %s"
    #     params.append(to_date)

    cursor = mysql.connection.cursor(MySQLdb.cursors.DictCursor)

    #projects for employees
    cursor.execute("""
        SELECT DISTINCT p.id, p.name
        FROM projects p
        LEFT JOIN project_members pm 
            ON pm.project_id = p.id
        LEFT JOIN assigned_tasks at 
            ON at.project_id = p.id 
            AND at.assigned_to_user_id = %s
        WHERE p.status != 'deleted'
        AND (
            FIND_IN_SET(%s, pm.user_ids)
            OR at.assigned_to_user_id IS NOT NULL
        )
    """, (user_id, user_id))

    
    projects = cursor.fetchall()

        # ---------------- PROJECT SUMMARY ----------------
    project_summary = None

    if selected_project:
        summary_query = """
            SELECT 
                p.id,
                p.name,
                p.created_at,
                p.deadline_date,
                p.status AS project_status,
                MIN(st.created_at) AS start_date,
                MAX(st.created_at) AS last_task_date,
                SUM(CASE WHEN st.status != 'completed' THEN 1 ELSE 0 END) AS pending_tasks
            FROM projects p
            LEFT JOIN submitted_tasks st ON st.project_id = p.id
            WHERE p.id = %s
            GROUP BY p.id
        """
        cursor.execute(summary_query, (selected_project,))
        project_summary = cursor.fetchone()

        project_status = project_summary['project_status']
        today = date.today()

        start_date = project_summary['start_date'].date() if project_summary['start_date'] else None
        due_date = project_summary['deadline_date']
        last_date = project_summary['last_task_date'].date() if project_summary['last_task_date'] else None

        if project_status == 'Completed':
            project_summary['status_text'] = "Completed"
            project_summary['status_class'] = "success"
            project_summary['completed_date'] = last_date
            project_summary['duration_days'] = (
                (last_date - start_date).days if start_date and last_date else 0
            )

        elif project_status == 'Ongoing':
            if due_date:
                diff_days = (due_date - today).days
                if diff_days >= 0:
                    project_summary['status_text'] = f"{diff_days} days remaining"
                    project_summary['status_class'] = "warning"
                else:
                    project_summary['status_text'] = f"Overdue by {abs(diff_days)} days"
                    project_summary['status_class'] = "danger"
            else:
                project_summary['status_text'] = "Ongoing (no deadline)"
                project_summary['status_class'] = "secondary"

        elif project_status == 'deleted':
            project_summary['status_text'] = "Deleted"
            project_summary['status_class'] = "dark"

    
     # ---------------- WHERE CLAUSE ----------------
    
    where_clause = "WHERE 1=1"
    params = []

    

    if selected_project:
        where_clause += " AND p.id = %s"
        params.append(selected_project)

    if from_date and to_date:
        where_clause += " AND DATE(st.created_at) BETWEEN %s AND %s"
        params.extend([from_date, to_date])
    elif from_date:
        where_clause += " AND DATE(st.created_at) >= %s"
        params.append(from_date)
    elif to_date:
        where_clause += " AND DATE(st.created_at) <= %s"
        params.append(to_date)

    chat_query = """
            SELECT
                at.id AS assigned_task_id,
                at.task_name,
                at.date_assigned,
                at.status AS assigned_task_status,

                ub.name AS assigned_by_name,
                ut.name AS assigned_to_name,

                st.id AS submission_id,
                st.task_description,
                st.status AS submission_status,
                st.created_at AS submission_date,
                us.name AS submitted_by_name,

                p.id AS project_id,
                p.name AS project_name

            FROM assigned_tasks at
                JOIN users ub ON at.assigned_by_user_id = ub.id
                JOIN users ut ON at.assigned_to_user_id = ut.id

                LEFT JOIN submitted_tasks st 
                    ON st.assigned_task_id = at.id
                    AND st.user_id = %s

                LEFT JOIN users us ON st.user_id = us.id
                JOIN projects p ON at.project_id = p.id

                LEFT JOIN project_members pm 
                    ON pm.project_id = p.id
                    AND FIND_IN_SET(%s, pm.user_ids)

                WHERE
                    at.assigned_to_user_id = %s
   
            """
    chat_params = [user_id, user_id, user_id]


    #projects
    if selected_project:
        chat_query += " AND at.project_id = %s"
        chat_params.append(selected_project)
    #Date Filters
    if from_date and to_date:
        chat_query += " AND ( st.created_at IS NULL OR DATE(st.created_at) BETWEEN %s AND %s )"
        chat_params.extend([from_date, to_date])
    elif from_date:
        chat_query += " AND ( st.created_at IS NULL OR DATE(st.created_at) >= %s )"
        chat_params.append(from_date)
    elif to_date:
        chat_query += " AND ( st.created_at IS NULL OR DATE(st.created_at) <= %s )"
        chat_params.append(to_date)

    cursor.execute(chat_query, tuple(chat_params))
    rows = cursor.fetchall()

    chat_tasks = {}

    for row in rows:
        task_id = row['assigned_task_id']

        if task_id not in chat_tasks:
            chat_tasks[task_id] = {
                'task_id': task_id,
                'task_name': row['task_name'],
                'assigned_by': row['assigned_by_name'],
                'assigned_to': row['assigned_to_name'],
                'date_assigned': row['date_assigned'],
                'task_status': row['assigned_task_status'],
                'project_name': row['project_name'],
                'submissions': []
            }

        if row['submission_id']:
            chat_tasks[task_id]['submissions'].append({
                'description': row['task_description'],
                'submitted_by': row['submitted_by_name'],
                'date': row['submission_date'],
                'status': row['submission_status']
            })





    
    cursor.close()

    return render_template(
        'employee/workreport.html',
        name=session['username'],
        
        project_summary=project_summary,
        projects=projects,
        selected_project=selected_project,
        chat_tasks=chat_tasks,
        
        from_date=from_date,
        to_date=to_date,
        endpoint='employee_workreport'
    )

# from flask import request, render_template, session, redirect, url_for, flash
#from math import ceil
# from datetime import date
# import MySQLdb

@app.route('/superadmin/project_report', methods=['GET'])
def employee_project_report():

    # 🔐 Allow only superadmin & head
    if 'logged_in' not in session or session.get('user_role') not in ['superadmin', 'head']:
        flash('Unauthorized access.', 'danger')
        return redirect(url_for('login'))
    
    def clean_str(val):
        if not val or not isinstance(val, str):
            return None

        val = val.strip()

        if val.lower() in ('none', 'null', ''):
            return None

        return val


    def clean_int(val):
        try:
            return int(val)
        except (TypeError, ValueError):
            return None

    user_role = session['user_role']
    user_id = clean_int(session.get('user_id'))
    head_id = clean_int(session.get('user_id'))

    # ---------------- FILTER INPUTS ----------------



    selected_department = clean_str(request.args.get('department'))
    selected_project = clean_int(request.args.get('project_id'))
    from_date = clean_str(request.args.get('from_date'))
    to_date = clean_str(request.args.get('to_date'))

    

    # page = request.args.get('page', 1, type=int)
    # per_page = 10
    # offset = (page - 1) * per_page

    cursor = mysql.connection.cursor(MySQLdb.cursors.DictCursor)

    # ---------------- DEPARTMENTS ----------------
   

    if user_role == 'head':
        cursor.execute("""
            SELECT DISTINCT department
            FROM projects
            WHERE project_head_id = %s
            AND department IS NOT NULL
            AND status != 'deleted'
        """, (head_id,))
    else:  # superadmin
        cursor.execute("""
            SELECT DISTINCT department
            FROM projects
            WHERE department IS NOT NULL
            AND status != 'deleted'
        """)

    departments = [row['department'] for row in cursor.fetchall()]



    # ---------------- PROJECTS (BY DEPARTMENT) ----------------
   
    project_params = []

    if user_role == 'head':
        project_query = """
            SELECT id, name
            FROM projects
            WHERE status != 'deleted'
            AND project_head_id = %s
        """
        project_params.append(head_id)

    else:  # superadmin
        project_query = """
            SELECT id, name
            FROM projects
            WHERE status != 'deleted'
        """

    if selected_department:
        project_query += " AND department = %s"
        project_params.append(selected_department)

    cursor.execute(project_query, tuple(project_params))
    projects = cursor.fetchall()



    # ---------------- PROJECT SUMMARY ----------------
    project_summary = None

    if selected_project:
        summary_query = """
            SELECT 
                p.id,
                p.name,
                p.created_at,
                p.deadline_date,
                MIN(st.created_at) AS start_date,
                MAX(st.created_at) AS last_task_date,
                SUM(CASE WHEN st.status != 'completed' THEN 1 ELSE 0 END) AS pending_tasks
            FROM projects p
            LEFT JOIN submitted_tasks st ON st.project_id = p.id
            WHERE p.id = %s
            GROUP BY p.id
        """
        cursor.execute(summary_query, (selected_project,))
        project_summary = cursor.fetchone()

        if project_summary:
            today = date.today()

            start_date = project_summary['start_date'].date() if project_summary['start_date'] else None
            due_date = project_summary['deadline_date']
            last_date = project_summary['last_task_date'].date() if project_summary['last_task_date'] else None

            if project_summary['pending_tasks'] == 0 and last_date:
                project_summary['status_text'] = "Completed"
                project_summary['status_class'] = "success"
                project_summary['completed_date'] = last_date
                project_summary['duration_days'] = (last_date - start_date).days if start_date else 0
            else:
                if due_date:
                    diff_days = (due_date - today).days
                    if diff_days >= 0:
                        project_summary['status_text'] = f"{diff_days} days remaining"
                        project_summary['status_class'] = "warning"
                    else:
                        project_summary['status_text'] = f"Overdue by {abs(diff_days)} days"
                        project_summary['status_class'] = "danger"
                else:
                    project_summary['status_text'] = "No deadline"
                    project_summary['status_class'] = "secondary"

    # ---------------- WHERE CLAUSE ----------------
    where_clause = "WHERE 1=1"
    params = []

    if selected_department:
        where_clause += " AND p.department = %s"
        params.append(selected_department)

    if selected_project:
        where_clause += " AND p.id = %s"
        params.append(selected_project)

    if from_date and to_date:
        where_clause += " AND DATE(st.created_at) BETWEEN %s AND %s"
        params.extend([from_date, to_date])
    elif from_date:
        where_clause += " AND DATE(st.created_at) >= %s"
        params.append(from_date)
    elif to_date:
        where_clause += " AND DATE(st.created_at) <= %s"
        params.append(to_date)

    # 🔒 Head sees only their employees
    if user_role == 'head':
        where_clause += " AND u.assigned_head = %s"
        params.append(user_id)

    
   

    # ---------------- DATA ----------------
    chat_query = """
    SELECT
        at.id AS assigned_task_id,
        at.task_name,
        at.date_assigned,
        at.status AS assigned_task_status,

        ub.name AS assigned_by_name,
        ut.name AS assigned_to_name,

        st.id AS submission_id,
        st.task_description,
        st.status AS submission_status,
        st.created_at AS submission_date,
        us.name AS submitted_by_name,

        p.id AS project_id,
        p.name AS project_name

        FROM assigned_tasks at
        JOIN users ub ON at.assigned_by_user_id = ub.id
        JOIN users ut ON at.assigned_to_user_id = ut.id
        LEFT JOIN submitted_tasks st ON st.assigned_task_id = at.id
        LEFT JOIN users us ON st.user_id = us.id
        LEFT JOIN projects p ON at.project_id = p.id
        WHERE 1=1
    """
    chat_params = []

    if user_role == 'head' and user_id:
        chat_query += """
        AND p.project_head_id = %s
        AND (
            at.assigned_by_user_id = %s
            OR at.assigned_to_user_id = %s
        )
    """
        chat_params.extend([user_id, user_id, user_id])

    if selected_department:
        chat_query += " AND p.department = %s"
        chat_params.append(selected_department)

    #Project Filter
    if selected_project:
        chat_query += " AND at.project_id = %s"
        chat_params.append(selected_project)
    
    #Date Filter
    if from_date and to_date:
        chat_query += " AND ( st.created_at IS NULL OR DATE(st.created_at) BETWEEN %s AND %s)"
        chat_params.extend([from_date, to_date])
    elif from_date:
        chat_query += " AND ( st.created_at IS NULL OR DATE(st.created_at) >= %s )"
        chat_params.append(from_date)
    elif to_date:
        chat_query += " AND ( st.created_at IS NULL OR DATE(st.created_at) <= %s )"
        chat_params.append(to_date)

    cursor.execute(chat_query, tuple(chat_params))
    rows = cursor.fetchall()

    chat_tasks = {}

    for row in rows:
        task_id = row['assigned_task_id']

        if task_id not in chat_tasks:
            chat_tasks[task_id] = {
                'task_id': task_id,
                'task_name': row['task_name'],
                'assigned_by': row['assigned_by_name'],
                'assigned_to': row['assigned_to_name'],
                'date_assigned': row['date_assigned'],
                'task_status': row['assigned_task_status'],
                'project_name': row['project_name'],
                'submissions': []
            }

        if row['submission_id']:
            chat_tasks[task_id]['submissions'].append({
                'description': row['task_description'],
                'submitted_by': row['submitted_by_name'],
                'date': row['submission_date'],
                'status': row['submission_status']
            })

    

    cursor.close()

    # ---------------- RENDER ----------------
    return render_template(
        'superadmin/project_report.html',
        chat_tasks=chat_tasks,
        project_summary=project_summary,
        departments=departments,
        projects=projects,
        selected_department=selected_department,
        selected_project=selected_project,
        from_date=from_date,
        to_date=to_date,
        # current_page=page,
        endpoint='employee_project_report'
    )

#today work section:
@app.route('/today-work', methods=['GET', 'POST'])
def today_work():
    if 'user_id' not in session:
        return redirect(url_for('login'))

    user_id = session['user_id']
    role = session['user_role']
    today = date.today()
    cursor = mysql.connection.cursor(DictCursor)

    if request.method == 'POST':
        data = request.get_json()
        work_text = data.get('today_work', '').strip()

        if role == 'superadmin':
            return jsonify({"status": "error", "message": "Superadmin cannot add work"})

        if not work_text:
            return jsonify({"status": "error", "message": "Work cannot be empty"})

        cursor.execute("""
            INSERT INTO today_work (user_id, employee_name, department, work, work_date)
            VALUES (%s, %s, %s, %s, %s)
        """, (user_id, session['username'], session['department'], work_text, today))

        mysql.connection.commit()
        cursor.close()
        return jsonify({"status": "success", "message": "Today's work added successfully"})

    # GET request (for modal content)
    today_works = []
    if role in ['employee', 'intern']:
        cursor.execute("SELECT employee_name, work FROM today_work WHERE user_id=%s AND work_date=%s", (user_id, today))
        today_works = cursor.fetchall()
    elif role == 'head':
        cursor.execute("""
            SELECT employee_name, work
            FROM today_work
            WHERE user_id = %s AND work_date = %s
            UNION ALL
            SELECT tw.employee_name, tw.work
            FROM today_work tw
            JOIN users u ON tw.user_id = u.id
            WHERE u.assigned_head = %s AND tw.work_date = %s
        """, (user_id, today, user_id, today))
        today_works = cursor.fetchall()
    elif role == 'superadmin':
        cursor.execute("SELECT employee_name, department, work FROM today_work WHERE work_date = %s", (today,))
        today_works = cursor.fetchall()

        #Fetch count
        # Count distinct users who submitted work today
  
    cursor.close()
    return render_template('today_work.html', today_works=today_works)

# @app.route('/employee/update_password', methods=['POST'])
# def employee_update_password():
#     if 'logged_in' not in session:
#         flash("Please log in to change your password.", "danger")
#         return redirect(url_for('login'))

#     user_id = session['user_id']
#     current_password = request.form.get('current_password')
#     new_password = request.form.get('new_password')
#     confirm_new_password = request.form.get('confirm_new_password')

#     cursor = mysql.connection.cursor()
#     cursor.execute("SELECT password FROM users WHERE id = %s", (user_id,))
#     user_data = cursor.fetchone()
#     cursor.close()

#     if not user_data or current_password != user_data['password']:
#         flash("❌ Current password incorrect.", "danger")
#         return redirect(url_for('employee_profile'))

#     if new_password != confirm_new_password:
#         flash("❌ New passwords do not match.", "danger")
#         return redirect(url_for('employee_profile'))

#     try:
#         cursor = mysql.connection.cursor()
#         cursor.execute(
#             "UPDATE users SET password = %s WHERE id = %s", (new_password, user_id))
#         mysql.connection.commit()
#         flash("✅ Password updated successfully!", "success")
#     except Exception as e:
#         mysql.connection.rollback()
#         flash(f"❌ Error updating password: {e}", "danger")
#     finally:
#         cursor.close()

#     return redirect(url_for('employee_profile'))


if __name__ == '__main__':
    app.run(debug=True)
