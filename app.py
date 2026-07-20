from __future__ import annotations

import csv
import io
import json
import os
import re
import secrets
import string
from datetime import datetime, timezone
from functools import wraps
from pathlib import Path
from typing import Any, Callable, Iterable, TypeVar

from docx import Document
from flask import (
    Flask,
    Response,
    abort,
    flash,
    g,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import UniqueConstraint, func
from sqlalchemy.exc import IntegrityError
from werkzeug.security import check_password_hash, generate_password_hash
from werkzeug.utils import secure_filename

BASE_DIR = Path(__file__).resolve().parent
CHOICE_MAP = {"①": 1, "②": 2, "③": 3, "④": 4, "⑤": 5}
CHOICE_SYMBOLS = {value: key for key, value in CHOICE_MAP.items()}
ROLES = {"director", "teacher", "student"}
ALLOWED_IMPORT_EXTENSIONS = {".docx"}
MAX_IMPORT_SIZE = 20 * 1024 * 1024


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def normalize_database_url(url: str) -> str:
    if url.startswith("postgres://"):
        return "postgresql+psycopg://" + url[len("postgres://") :]
    if url.startswith("postgresql://"):
        return "postgresql+psycopg://" + url[len("postgresql://") :]
    return url


def create_app(test_config: dict[str, Any] | None = None) -> Flask:
    app = Flask(__name__)
    default_db = f"sqlite:///{BASE_DIR / 'thesophie.db'}"
    app.config.update(
        SECRET_KEY=os.getenv("SECRET_KEY", "dev-change-this-key"),
        SQLALCHEMY_DATABASE_URI=normalize_database_url(os.getenv("DATABASE_URL", default_db)),
        SQLALCHEMY_TRACK_MODIFICATIONS=False,
        MAX_CONTENT_LENGTH=MAX_IMPORT_SIZE,
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=os.getenv("COOKIE_SECURE", "0") == "1",
        PERMANENT_SESSION_LIFETIME=60 * 60 * 12,
    )
    if test_config:
        app.config.update(test_config)

    db.init_app(app)

    with app.app_context():
        db.create_all()
        ensure_seed_data()

    register_context(app)
    register_security(app)
    register_routes(app)
    register_errors(app)
    return app


db = SQLAlchemy()
F = TypeVar("F", bound=Callable[..., Any])


class User(db.Model):
    __tablename__ = "users"
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False, index=True)
    password_hash = db.Column(db.String(255), nullable=False)
    role = db.Column(db.String(20), nullable=False, index=True)
    active = db.Column(db.Boolean, nullable=False, default=True)
    must_change_password = db.Column(db.Boolean, nullable=False, default=True)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow)
    last_login_at = db.Column(db.DateTime(timezone=True))

    student_profile = db.relationship(
        "StudentProfile", back_populates="user", uselist=False, cascade="all, delete-orphan"
    )

    def set_password(self, raw: str) -> None:
        self.password_hash = generate_password_hash(raw)

    def check_password(self, raw: str) -> bool:
        return check_password_hash(self.password_hash, raw)


class StudentProfile(db.Model):
    __tablename__ = "student_profiles"
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"), unique=True, nullable=False)
    name = db.Column(db.String(80), nullable=False, index=True)
    school = db.Column(db.String(120), nullable=False, default="")
    grade = db.Column(db.String(40), nullable=False, default="고3")
    class_name = db.Column(db.String(80), nullable=False, default="")
    parent_phone = db.Column(db.String(40), nullable=False, default="")
    memo = db.Column(db.Text, nullable=False, default="")
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow)

    user = db.relationship("User", back_populates="student_profile")
    submissions = db.relationship("Submission", back_populates="student", cascade="all, delete-orphan")


class Worksheet(db.Model):
    __tablename__ = "worksheets"
    id = db.Column(db.Integer, primary_key=True)
    number = db.Column(db.Integer, unique=True, nullable=False, index=True)
    title = db.Column(db.String(180), nullable=False)
    subtitle = db.Column(db.String(500), nullable=False, default="")
    recommended_minutes = db.Column(db.Integer, nullable=False, default=60)
    original_questions = db.Column(db.JSON, nullable=False, default=list)
    transformed_questions = db.Column(db.JSON, nullable=False, default=list)
    concept_answers = db.Column(db.JSON, nullable=False, default=dict)
    published = db.Column(db.Boolean, nullable=False, default=True)
    imported_at = db.Column(db.DateTime(timezone=True))
    updated_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow)

    submissions = db.relationship("Submission", back_populates="worksheet", cascade="all, delete-orphan")

    @property
    def original_count(self) -> int:
        return len(self.original_questions or [])

    @property
    def transformed_count(self) -> int:
        return len(self.transformed_questions or [])

    @property
    def concept_count(self) -> int:
        return sum(len(values) for values in (self.concept_answers or {}).values())

    @property
    def ready(self) -> bool:
        return self.original_count > 0 and self.transformed_count > 0 and self.concept_count > 0


class Submission(db.Model):
    __tablename__ = "submissions"
    __table_args__ = (UniqueConstraint("student_id", "worksheet_id", "kind", name="uq_submission"),)
    id = db.Column(db.Integer, primary_key=True)
    student_id = db.Column(db.Integer, db.ForeignKey("student_profiles.id", ondelete="CASCADE"), nullable=False, index=True)
    worksheet_id = db.Column(db.Integer, db.ForeignKey("worksheets.id", ondelete="CASCADE"), nullable=False, index=True)
    kind = db.Column(db.String(20), nullable=False)  # original | transformed
    answers = db.Column(db.JSON, nullable=False, default=list)
    correct_count = db.Column(db.Integer, nullable=False, default=0)
    total_count = db.Column(db.Integer, nullable=False, default=0)
    score = db.Column(db.Float, nullable=False, default=0)
    submitted_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow)

    student = db.relationship("StudentProfile", back_populates="submissions")
    worksheet = db.relationship("Worksheet", back_populates="submissions")


class ImportLog(db.Model):
    __tablename__ = "import_logs"
    id = db.Column(db.Integer, primary_key=True)
    filename = db.Column(db.String(255), nullable=False)
    worksheet_count = db.Column(db.Integer, nullable=False, default=0)
    question_count = db.Column(db.Integer, nullable=False, default=0)
    imported_by = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow)


def ensure_seed_data() -> None:
    if Worksheet.query.count() == 0:
        for number in range(1, 21):
            db.session.add(
                Worksheet(
                    number=number,
                    title=f"고3 매일 학습지 {number}회차",
                    subtitle="원본 학습지 업로드 후 정답·해설이 자동 등록됩니다.",
                    recommended_minutes=60,
                    original_questions=[],
                    transformed_questions=[],
                    concept_answers={},
                    published=True,
                )
            )

    initial_accounts = [
        (
            os.getenv("DIRECTOR_ID", "director"),
            os.getenv("DIRECTOR_PASSWORD", "ChangeMe-Director-2026"),
            "director",
        ),
        (
            os.getenv("TEACHER_ID", "teacher"),
            os.getenv("TEACHER_PASSWORD", "ChangeMe-Teacher-2026"),
            "teacher",
        ),
    ]
    for username, password, role in initial_accounts:
        if not User.query.filter(func.lower(User.username) == username.lower()).first():
            user = User(username=username, role=role, active=True, must_change_password=True)
            user.set_password(password)
            db.session.add(user)
    db.session.commit()


def register_context(app: Flask) -> None:
    @app.context_processor
    def inject_globals() -> dict[str, Any]:
        return {
            "csrf_token": get_csrf_token,
            "choice_symbols": CHOICE_SYMBOLS,
            "role_label": {"director": "원장", "teacher": "선생님", "student": "학생"},
        }

    @app.before_request
    def load_user() -> None:
        g.user = None
        user_id = session.get("user_id")
        if user_id:
            user = db.session.get(User, user_id)
            if user and user.active:
                g.user = user
            else:
                session.clear()


def register_security(app: Flask) -> None:
    @app.before_request
    def verify_csrf() -> None:
        if request.method in {"POST", "PUT", "PATCH", "DELETE"}:
            sent = request.form.get("_csrf") or request.headers.get("X-CSRF-Token")
            expected = session.get("csrf_token")
            if not expected or not sent or not secrets.compare_digest(str(sent), str(expected)):
                abort(400, description="보안 토큰이 만료되었습니다. 화면을 새로고침해 주세요.")


def get_csrf_token() -> str:
    token = session.get("csrf_token")
    if not token:
        token = secrets.token_urlsafe(32)
        session["csrf_token"] = token
    return token


def login_required(view: F) -> F:
    @wraps(view)
    def wrapped(*args: Any, **kwargs: Any) -> Any:
        if not g.user:
            return redirect(url_for("login", next=request.full_path.rstrip("?")))
        return view(*args, **kwargs)

    return wrapped  # type: ignore[return-value]


def roles_required(*roles: str) -> Callable[[F], F]:
    def decorator(view: F) -> F:
        @wraps(view)
        def wrapped(*args: Any, **kwargs: Any) -> Any:
            if not g.user:
                return redirect(url_for("login"))
            if g.user.role not in roles:
                abort(403)
            return view(*args, **kwargs)

        return wrapped  # type: ignore[return-value]

    return decorator


def safe_next_url(target: str | None) -> str | None:
    if not target or not target.startswith("/") or target.startswith("//"):
        return None
    return target


def generate_password(length: int = 12) -> str:
    alphabet = string.ascii_letters + string.digits
    while True:
        value = "".join(secrets.choice(alphabet) for _ in range(length))
        if any(c.islower() for c in value) and any(c.isupper() for c in value) and any(c.isdigit() for c in value):
            return value


def next_student_username() -> str:
    rows = db.session.execute(db.select(User.username).where(User.role == "student")).scalars().all()
    max_no = 0
    for username in rows:
        match = re.fullmatch(r"STU(\d{4,})", username.upper())
        if match:
            max_no = max(max_no, int(match.group(1)))
    return f"STU{max_no + 1:04d}"


def parse_choices(raw_values: Iterable[str], expected: int) -> list[int]:
    values: list[int] = []
    for raw in raw_values:
        try:
            value = int(raw)
        except (TypeError, ValueError):
            value = 0
        values.append(value if value in {1, 2, 3, 4, 5} else 0)
    if len(values) != expected or any(value == 0 for value in values):
        raise ValueError("모든 문항의 답을 선택해 주세요.")
    return values


def score_answers(questions: list[dict[str, Any]], answers: list[int]) -> tuple[int, float, list[dict[str, Any]]]:
    results: list[dict[str, Any]] = []
    correct = 0
    for index, question in enumerate(questions):
        answer = answers[index]
        expected = int(question["answer"])
        is_correct = answer == expected
        if is_correct:
            correct += 1
        results.append(
            {
                "number": question.get("number", index + 1),
                "label": question.get("label", f"{index + 1}번"),
                "answer": answer,
                "correct_answer": expected,
                "correct": is_correct,
                "explanation": question.get("explanation", "해설이 등록되지 않았습니다."),
            }
        )
    score = round(correct / len(questions) * 100, 1) if questions else 0.0
    return correct, score, results


def get_student_submission(student_id: int, worksheet_id: int, kind: str) -> Submission | None:
    return Submission.query.filter_by(student_id=student_id, worksheet_id=worksheet_id, kind=kind).first()


def student_average(student_id: int, kind: str) -> float | None:
    value = db.session.execute(
        db.select(func.avg(Submission.score)).where(Submission.student_id == student_id, Submission.kind == kind)
    ).scalar()
    return round(float(value), 1) if value is not None else None


def worksheet_status(student_id: int, worksheet: Worksheet) -> dict[str, Any]:
    original = get_student_submission(student_id, worksheet.id, "original")
    transformed = get_student_submission(student_id, worksheet.id, "transformed")
    return {
        "original": original,
        "transformed": transformed,
        "unlocked": bool(original and transformed),
        "complete": bool(original and transformed),
    }


def register_routes(app: Flask) -> None:
    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.route("/login", methods=["GET", "POST"])
    def login() -> Any:
        if g.user:
            return redirect(url_for("home"))
        if request.method == "POST":
            username = request.form.get("username", "").strip()
            password = request.form.get("password", "")
            user = User.query.filter(func.lower(User.username) == username.lower()).first()
            if not user or not user.active or not user.check_password(password):
                flash("아이디 또는 비밀번호가 올바르지 않습니다.", "error")
            else:
                session.clear()
                session.permanent = True
                session["user_id"] = user.id
                session["csrf_token"] = secrets.token_urlsafe(32)
                user.last_login_at = utcnow()
                db.session.commit()
                target = safe_next_url(request.args.get("next"))
                if user.must_change_password:
                    return redirect(url_for("change_password"))
                return redirect(target or url_for("home"))
        return render_template("login.html")

    @app.post("/logout")
    @login_required
    def logout() -> Any:
        session.clear()
        return redirect(url_for("login"))

    @app.get("/")
    @login_required
    def home() -> Any:
        if g.user.role == "student":
            return redirect(url_for("student_dashboard"))
        return redirect(url_for("staff_dashboard"))

    @app.route("/account/password", methods=["GET", "POST"])
    @login_required
    def change_password() -> Any:
        if request.method == "POST":
            current = request.form.get("current_password", "")
            new_password = request.form.get("new_password", "")
            confirm = request.form.get("confirm_password", "")
            if not g.user.check_password(current):
                flash("현재 비밀번호가 올바르지 않습니다.", "error")
            elif len(new_password) < 10 or new_password.isalpha() or new_password.isdigit():
                flash("새 비밀번호는 영문과 숫자를 포함하여 10자 이상이어야 합니다.", "error")
            elif new_password != confirm:
                flash("새 비밀번호 확인이 일치하지 않습니다.", "error")
            else:
                g.user.set_password(new_password)
                g.user.must_change_password = False
                db.session.commit()
                flash("비밀번호를 변경했습니다.", "success")
                return redirect(url_for("home"))
        return render_template("change_password.html")

    @app.get("/student")
    @roles_required("student")
    def student_dashboard() -> Any:
        profile = g.user.student_profile
        worksheets = Worksheet.query.filter_by(published=True).order_by(Worksheet.number).all()
        cards = [(worksheet, worksheet_status(profile.id, worksheet)) for worksheet in worksheets]
        return render_template(
            "student_dashboard.html",
            profile=profile,
            cards=cards,
            original_average=student_average(profile.id, "original"),
            transformed_average=student_average(profile.id, "transformed"),
        )

    @app.get("/student/worksheet/<int:number>")
    @roles_required("student")
    def student_worksheet(number: int) -> Any:
        worksheet = Worksheet.query.filter_by(number=number, published=True).first_or_404()
        profile = g.user.student_profile
        status = worksheet_status(profile.id, worksheet)
        return render_template("worksheet.html", worksheet=worksheet, status=status, profile=profile)

    @app.route("/student/worksheet/<int:number>/<kind>", methods=["GET", "POST"])
    @roles_required("student")
    def answer_sheet(number: int, kind: str) -> Any:
        if kind not in {"original", "transformed"}:
            abort(404)
        worksheet = Worksheet.query.filter_by(number=number, published=True).first_or_404()
        questions = worksheet.original_questions if kind == "original" else worksheet.transformed_questions
        if not questions:
            flash("선생님이 아직 이 답안지를 등록하지 않았습니다.", "error")
            return redirect(url_for("student_worksheet", number=number))
        profile = g.user.student_profile
        existing = get_student_submission(profile.id, worksheet.id, kind)
        if request.method == "POST":
            try:
                answers = parse_choices(request.form.getlist("answer"), len(questions))
            except ValueError as exc:
                flash(str(exc), "error")
                return render_template(
                    "answer_sheet.html",
                    worksheet=worksheet,
                    kind=kind,
                    questions=questions,
                    existing=existing,
                )
            correct_count, score, _results = score_answers(questions, answers)
            if existing:
                existing.answers = answers
                existing.correct_count = correct_count
                existing.total_count = len(questions)
                existing.score = score
                existing.submitted_at = utcnow()
            else:
                existing = Submission(
                    student_id=profile.id,
                    worksheet_id=worksheet.id,
                    kind=kind,
                    answers=answers,
                    correct_count=correct_count,
                    total_count=len(questions),
                    score=score,
                    submitted_at=utcnow(),
                )
                db.session.add(existing)
            db.session.commit()
            flash("채점이 완료되었습니다.", "success")
            return redirect(url_for("submission_result", submission_id=existing.id))
        return render_template(
            "answer_sheet.html", worksheet=worksheet, kind=kind, questions=questions, existing=existing
        )

    @app.get("/student/result/<int:submission_id>")
    @roles_required("student")
    def submission_result(submission_id: int) -> Any:
        submission = db.session.get(Submission, submission_id)
        if not submission or submission.student.user_id != g.user.id:
            abort(404)
        questions = (
            submission.worksheet.original_questions
            if submission.kind == "original"
            else submission.worksheet.transformed_questions
        )
        _correct, _score, results = score_answers(questions, submission.answers)
        status = worksheet_status(submission.student_id, submission.worksheet_id)
        return render_template("result.html", submission=submission, results=results, status=status)

    @app.get("/student/worksheet/<int:number>/concepts")
    @roles_required("student")
    def concept_answers(number: int) -> Any:
        worksheet = Worksheet.query.filter_by(number=number, published=True).first_or_404()
        profile = g.user.student_profile
        status = worksheet_status(profile.id, worksheet)
        if not status["unlocked"]:
            flash("모고·모평 답안과 변형 문제 답안을 모두 제출하면 괄호형 정답이 공개됩니다.", "error")
            return redirect(url_for("student_worksheet", number=number))
        return render_template("concept_answers.html", worksheet=worksheet, profile=profile)

    @app.get("/student/report/<int:number>")
    @roles_required("student")
    def student_report(number: int) -> Any:
        worksheet = Worksheet.query.filter_by(number=number).first_or_404()
        profile = g.user.student_profile
        original = get_student_submission(profile.id, worksheet.id, "original")
        transformed = get_student_submission(profile.id, worksheet.id, "transformed")
        if not original and not transformed:
            abort(404)
        return render_template(
            "report.html",
            profile=profile,
            worksheet=worksheet,
            original=original,
            transformed=transformed,
            original_average=student_average(profile.id, "original"),
            transformed_average=student_average(profile.id, "transformed"),
            staff_view=False,
        )

    @app.get("/staff")
    @roles_required("director", "teacher")
    def staff_dashboard() -> Any:
        students = StudentProfile.query.join(User).filter(User.active.is_(True)).order_by(StudentProfile.name).all()
        worksheets = Worksheet.query.order_by(Worksheet.number).all()
        recent_submissions = Submission.query.order_by(Submission.submitted_at.desc()).limit(12).all()
        readiness = sum(1 for worksheet in worksheets if worksheet.ready)
        rows = []
        for student in students:
            progress = []
            completed = 0
            for worksheet in worksheets:
                original = get_student_submission(student.id, worksheet.id, "original")
                transformed = get_student_submission(student.id, worksheet.id, "transformed")
                state = 2 if original and transformed else 1 if original or transformed else 0
                completed += 1 if state == 2 else 0
                progress.append({"number": worksheet.number, "state": state})
            rows.append(
                {
                    "student": student,
                    "original_average": student_average(student.id, "original"),
                    "transformed_average": student_average(student.id, "transformed"),
                    "completed": completed,
                    "progress": progress,
                }
            )
        issued = session.pop("issued_accounts", None)
        return render_template(
            "staff_dashboard.html",
            rows=rows,
            worksheets=worksheets,
            recent_submissions=recent_submissions,
            readiness=readiness,
            issued=issued,
        )

    @app.route("/staff/students/new", methods=["GET", "POST"])
    @roles_required("director", "teacher")
    def create_student() -> Any:
        if request.method == "POST":
            name = request.form.get("name", "").strip()
            if not name:
                flash("학생 이름을 입력해 주세요.", "error")
                return render_template("student_form.html")
            username = request.form.get("username", "").strip().upper() or next_student_username()
            password = request.form.get("password", "").strip() or generate_password()
            if User.query.filter(func.lower(User.username) == username.lower()).first():
                flash("이미 사용 중인 아이디입니다.", "error")
                return render_template("student_form.html")
            user = User(username=username, role="student", active=True, must_change_password=True)
            user.set_password(password)
            profile = StudentProfile(
                user=user,
                name=name,
                school=request.form.get("school", "").strip(),
                grade=request.form.get("grade", "고3").strip(),
                class_name=request.form.get("class_name", "").strip(),
                parent_phone=request.form.get("parent_phone", "").strip(),
                memo=request.form.get("memo", "").strip(),
            )
            db.session.add(profile)
            db.session.commit()
            session["issued_accounts"] = [{"name": name, "username": username, "password": password}]
            flash(f"{name} 학생 계정을 발급했습니다.", "success")
            return redirect(url_for("staff_dashboard"))
        return render_template("student_form.html")

    @app.post("/staff/students/import")
    @roles_required("director", "teacher")
    def import_students_csv() -> Any:
        uploaded = request.files.get("file")
        if not uploaded or not uploaded.filename:
            flash("CSV 파일을 선택해 주세요.", "error")
            return redirect(url_for("staff_dashboard"))
        try:
            text = uploaded.read().decode("utf-8-sig")
        except UnicodeDecodeError:
            flash("CSV 파일을 UTF-8 형식으로 저장해 주세요.", "error")
            return redirect(url_for("staff_dashboard"))
        reader = csv.DictReader(io.StringIO(text))
        if not reader.fieldnames or "name" not in reader.fieldnames:
            flash("CSV에는 name 열이 필요합니다.", "error")
            return redirect(url_for("staff_dashboard"))
        issued: list[dict[str, str]] = []
        try:
            for row in reader:
                name = (row.get("name") or "").strip()
                if not name:
                    continue
                username = (row.get("username") or "").strip().upper() or next_student_username()
                password = (row.get("password") or "").strip() or generate_password()
                if User.query.filter(func.lower(User.username) == username.lower()).first():
                    raise ValueError(f"중복 아이디: {username}")
                user = User(username=username, role="student", active=True, must_change_password=True)
                user.set_password(password)
                db.session.add(
                    StudentProfile(
                        user=user,
                        name=name,
                        school=(row.get("school") or "").strip(),
                        grade=(row.get("grade") or "고3").strip(),
                        class_name=(row.get("class_name") or "").strip(),
                        parent_phone=(row.get("parent_phone") or "").strip(),
                        memo=(row.get("memo") or "").strip(),
                    )
                )
                db.session.flush()
                issued.append({"name": name, "username": username, "password": password})
            db.session.commit()
        except (ValueError, IntegrityError) as exc:
            db.session.rollback()
            flash(f"학생 일괄 등록을 중단했습니다: {exc}", "error")
            return redirect(url_for("staff_dashboard"))
        session["issued_accounts"] = issued
        flash(f"{len(issued)}명의 학생 계정을 발급했습니다.", "success")
        return redirect(url_for("staff_dashboard"))

    @app.get("/staff/issued-accounts.csv")
    @roles_required("director", "teacher")
    def issued_accounts_csv() -> Response:
        issued = session.get("issued_accounts") or []
        output = io.StringIO()
        writer = csv.DictWriter(output, fieldnames=["name", "username", "password"])
        writer.writeheader()
        writer.writerows(issued)
        return Response(
            "\ufeff" + output.getvalue(),
            mimetype="text/csv; charset=utf-8",
            headers={"Content-Disposition": "attachment; filename=student_accounts.csv"},
        )

    @app.get("/staff/students/<int:student_id>")
    @roles_required("director", "teacher")
    def staff_student_detail(student_id: int) -> Any:
        profile = db.session.get(StudentProfile, student_id)
        if not profile:
            abort(404)
        worksheets = Worksheet.query.order_by(Worksheet.number).all()
        records = []
        for worksheet in worksheets:
            records.append(
                {
                    "worksheet": worksheet,
                    "original": get_student_submission(profile.id, worksheet.id, "original"),
                    "transformed": get_student_submission(profile.id, worksheet.id, "transformed"),
                }
            )
        return render_template(
            "staff_student_detail.html",
            profile=profile,
            records=records,
            original_average=student_average(profile.id, "original"),
            transformed_average=student_average(profile.id, "transformed"),
        )

    @app.post("/staff/students/<int:student_id>/reset-password")
    @roles_required("director", "teacher")
    def reset_student_password(student_id: int) -> Any:
        profile = db.session.get(StudentProfile, student_id)
        if not profile:
            abort(404)
        password = generate_password()
        profile.user.set_password(password)
        profile.user.must_change_password = True
        db.session.commit()
        session["issued_accounts"] = [
            {"name": profile.name, "username": profile.user.username, "password": password}
        ]
        flash("학생 비밀번호를 재발급했습니다.", "success")
        return redirect(url_for("staff_dashboard"))

    @app.post("/staff/students/<int:student_id>/toggle")
    @roles_required("director")
    def toggle_student(student_id: int) -> Any:
        profile = db.session.get(StudentProfile, student_id)
        if not profile:
            abort(404)
        profile.user.active = not profile.user.active
        db.session.commit()
        flash("학생 계정 상태를 변경했습니다.", "success")
        return redirect(url_for("staff_student_detail", student_id=student_id))

    @app.get("/staff/students/<int:student_id>/report/<int:number>")
    @roles_required("director", "teacher")
    def staff_report(student_id: int, number: int) -> Any:
        profile = db.session.get(StudentProfile, student_id)
        worksheet = Worksheet.query.filter_by(number=number).first_or_404()
        if not profile:
            abort(404)
        return render_template(
            "report.html",
            profile=profile,
            worksheet=worksheet,
            original=get_student_submission(profile.id, worksheet.id, "original"),
            transformed=get_student_submission(profile.id, worksheet.id, "transformed"),
            original_average=student_average(profile.id, "original"),
            transformed_average=student_average(profile.id, "transformed"),
            staff_view=True,
        )

    @app.route("/staff/workbook/import", methods=["GET", "POST"])
    @roles_required("director")
    def import_workbook() -> Any:
        if request.method == "POST":
            uploaded = request.files.get("file")
            if not uploaded or not uploaded.filename:
                flash("고3 매일학습지 DOCX 파일을 선택해 주세요.", "error")
                return render_template("import_workbook.html")
            suffix = Path(secure_filename(uploaded.filename)).suffix.lower()
            if suffix not in ALLOWED_IMPORT_EXTENSIONS:
                flash("DOCX 파일만 업로드할 수 있습니다.", "error")
                return render_template("import_workbook.html")
            try:
                parsed = parse_daily_workbook(uploaded.stream)
                summary = save_parsed_workbook(parsed)
                db.session.add(
                    ImportLog(
                        filename=secure_filename(uploaded.filename),
                        worksheet_count=summary["worksheets"],
                        question_count=summary["questions"],
                        imported_by=g.user.id,
                    )
                )
                db.session.commit()
            except Exception as exc:  # importer needs a useful admin-facing failure
                db.session.rollback()
                app.logger.exception("Workbook import failed")
                flash(f"학습지 분석에 실패했습니다: {exc}", "error")
                return render_template("import_workbook.html")
            flash(
                f"{summary['worksheets']}개 회차와 객관식 {summary['questions']}문항을 등록했습니다.",
                "success",
            )
            return redirect(url_for("staff_dashboard"))
        logs = ImportLog.query.order_by(ImportLog.created_at.desc()).limit(10).all()
        return render_template("import_workbook.html", logs=logs)

    @app.post("/staff/worksheets/<int:number>/publish")
    @roles_required("director")
    def toggle_publish(number: int) -> Any:
        worksheet = Worksheet.query.filter_by(number=number).first_or_404()
        worksheet.published = not worksheet.published
        db.session.commit()
        flash("회차 공개 상태를 변경했습니다.", "success")
        return redirect(url_for("staff_dashboard"))

    @app.get("/staff/sample-students.csv")
    @roles_required("director", "teacher")
    def sample_students_csv() -> Response:
        content = (
            "\ufeffname,username,password,school,grade,class_name,parent_phone,memo\n"
            "김학생,,,배곧고,고3,A반,010-0000-0000,독서 보강\n"
            "이학생,,,함현고,고3,B반,010-1111-1111,화작 집중\n"
        )
        return Response(
            content,
            mimetype="text/csv; charset=utf-8",
            headers={"Content-Disposition": "attachment; filename=student_import_sample.csv"},
        )


def register_errors(app: Flask) -> None:
    @app.errorhandler(400)
    def bad_request(error: Any) -> tuple[str, int]:
        return render_template("error.html", message=getattr(error, "description", "잘못된 요청입니다.")), 400

    @app.errorhandler(403)
    def forbidden(_error: Any) -> tuple[str, int]:
        return render_template("error.html", message="이 화면에 접근할 권한이 없습니다."), 403

    @app.errorhandler(404)
    def not_found(_error: Any) -> tuple[str, int]:
        return render_template("error.html", message="요청한 화면을 찾을 수 없습니다."), 404

    @app.errorhandler(413)
    def too_large(_error: Any) -> tuple[str, int]:
        return render_template("error.html", message="업로드 파일은 20MB 이하여야 합니다."), 413


def extract_docx_text(stream: Any) -> str:
    document = Document(stream)
    chunks: list[str] = []
    from docx.table import Table
    from docx.text.paragraph import Paragraph

    for block in iter_docx_blocks(document):
        if isinstance(block, Paragraph):
            text = block.text.strip()
            if text:
                chunks.append(text)
        elif isinstance(block, Table):
            for row in block.rows:
                cells = [normalize_space(cell.text) for cell in row.cells]
                if any(cells):
                    chunks.append(" | ".join(cells))
    text = "\n".join(chunks)
    text = text.replace("고3 매일 학습지 ·", "고3 매일 학습지")
    return normalize_import_text(text)


def iter_docx_blocks(document: Document) -> Iterable[Any]:
    from docx.table import Table
    from docx.text.paragraph import Paragraph
    from docx.oxml.table import CT_Tbl
    from docx.oxml.text.paragraph import CT_P

    parent = document.element.body
    for child in parent.iterchildren():
        if isinstance(child, CT_P):
            yield Paragraph(child, document)
        elif isinstance(child, CT_Tbl):
            yield Table(child, document)


def normalize_space(value: str) -> str:
    return re.sub(r"[ \t\u00a0]+", " ", value).strip()


def normalize_import_text(text: str) -> str:
    text = text.replace("\r", "\n")
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = re.sub(r"([가-힣A-Za-z0-9])\s*고3 매일 학습지\s*([· ]*)\s*(\d+)회차", r"\1\n고3 매일 학습지 \3회차", text)
    text = re.sub(r"(\d+)\s*회차\s*정답\s*및\s*해설", r"\1회차 정답 및 해설", text)
    text = text.replace("1 원문 문제 정답 및 해설", "1. 원문 문제 정답 및 해설")
    text = text.replace("2 핵심 개념 괄호형 정답", "2. 핵심 개념 괄호형 정답")
    text = text.replace("3 변형 문제 정답 및 해설", "3. 변형 문제 정답 및 해설")
    return text


def parse_daily_workbook(stream: Any) -> dict[int, dict[str, Any]]:
    text = extract_docx_text(stream)
    sessions = parse_session_headers(text)
    answer_starts = list(re.finditer(r"(?m)^\s*(\d{1,2})회차 정답 및 해설\s*$", text))
    if not answer_starts:
        raise ValueError("‘N회차 정답 및 해설’ 구역을 찾지 못했습니다.")

    parsed: dict[int, dict[str, Any]] = {}
    for index, match in enumerate(answer_starts):
        number = int(match.group(1))
        end = answer_starts[index + 1].start() if index + 1 < len(answer_starts) else len(text)
        section = text[match.end() : end]
        original_section, concept_section, transformed_section = split_answer_section(section)
        original = parse_original_answers(original_section)
        concepts = parse_concept_answers(concept_section)
        transformed = parse_transformed_answers(transformed_section)
        if not original and not transformed:
            continue
        header = sessions.get(number, {})
        parsed[number] = {
            "number": number,
            "title": header.get("title") or f"고3 매일 학습지 {number}회차",
            "subtitle": header.get("subtitle") or "",
            "recommended_minutes": header.get("recommended_minutes") or 60,
            "original_questions": original,
            "transformed_questions": transformed,
            "concept_answers": concepts,
        }
    if len(parsed) < 1:
        raise ValueError("정답 데이터를 추출하지 못했습니다.")
    return parsed


def parse_session_headers(text: str) -> dict[int, dict[str, Any]]:
    headers: dict[int, dict[str, Any]] = {}
    pattern = re.compile(r"(?m)^\s*고3 매일 학습지\s+(\d{1,2})회차\s*$")
    matches = list(pattern.finditer(text))
    for index, match in enumerate(matches):
        number = int(match.group(1))
        if number in headers:
            continue
        end = matches[index + 1].start() if index + 1 < len(matches) else min(len(text), match.end() + 2000)
        block = text[match.end() : min(end, match.end() + 1800)]
        minutes_match = re.search(r"권장\s*시간\s*(\d+)분", block)
        topic_match = re.search(r"문법\s*:\s*(.+?)\s*[·|]\s*화작\s*:\s*(.+?)\s*[·|]\s*문학\s*:\s*(.+?)\s*[·|]\s*독서\s*:\s*([^\n]+)", block)
        subtitle = ""
        if topic_match:
            subtitle = " · ".join(normalize_space(item) for item in topic_match.groups())
        headers[number] = {
            "title": f"고3 매일 학습지 {number}회차",
            "subtitle": subtitle,
            "recommended_minutes": int(minutes_match.group(1)) if minutes_match else 60,
        }
    return headers


def split_answer_section(section: str) -> tuple[str, str, str]:
    original_marker = re.search(r"1\.?\s*원문 문제 정답 및 해설", section)
    concept_marker = re.search(r"2\.?\s*핵심 개념 괄호형 정답", section)
    transformed_marker = re.search(r"3\.?\s*변형 문제 정답 및 해설", section)
    if not original_marker or not concept_marker or not transformed_marker:
        raise ValueError("정답 구역의 1·2·3번 제목을 모두 찾지 못했습니다.")
    original = section[original_marker.end() : concept_marker.start()]
    concepts = section[concept_marker.end() : transformed_marker.start()]
    transformed = section[transformed_marker.end() :]
    return original, concepts, transformed


def parse_original_answers(section: str) -> list[dict[str, Any]]:
    matches = list(re.finditer(r"(?m)^\s*(\d{1,2})번\s*([①②③④⑤])\s*$", section))
    questions: list[dict[str, Any]] = []
    for index, match in enumerate(matches):
        number = int(match.group(1))
        end = matches[index + 1].start() if index + 1 < len(matches) else len(section)
        explanation = clean_explanation(section[match.end() : end])
        questions.append(
            {
                "number": number,
                "label": f"{number}번",
                "answer": CHOICE_MAP[match.group(2)],
                "explanation": explanation,
            }
        )
    return questions


def parse_transformed_answers(section: str) -> list[dict[str, Any]]:
    pattern = re.compile(r"(?m)^\s*원문\s*(\d{1,2})번\s*변형\s*([12])\s*([①②③④⑤])\s*$")
    matches = list(pattern.finditer(section))
    questions: list[dict[str, Any]] = []
    sequence = 0
    for index, match in enumerate(matches):
        sequence += 1
        source_number = int(match.group(1))
        variant = int(match.group(2))
        end = matches[index + 1].start() if index + 1 < len(matches) else len(section)
        explanation = clean_explanation(section[match.end() : end])
        questions.append(
            {
                "number": sequence,
                "label": f"원문 {source_number}번 변형 {variant}",
                "source_number": source_number,
                "variant": variant,
                "answer": CHOICE_MAP[match.group(3)],
                "explanation": explanation,
            }
        )
    return questions


def parse_concept_answers(section: str) -> dict[str, list[dict[str, Any]]]:
    aliases = {
        "문법": "문법",
        "화법과 작문": "화법과 작문",
        "화작": "화법과 작문",
        "문학": "문학",
        "독서": "독서",
    }
    category_pattern = re.compile(r"\[(문법|화법과 작문|화작|문학|독서)\s*15\]")
    matches = list(category_pattern.finditer(section))
    output: dict[str, list[dict[str, Any]]] = {}
    for index, match in enumerate(matches):
        category = aliases[match.group(1)]
        end = matches[index + 1].start() if index + 1 < len(matches) else len(section)
        block = section[match.end() : end]
        entries = []
        answer_matches = list(re.finditer(r"(?:^|[/\n])\s*(\d{1,2})\.\s*([^/\n]+)", block))
        for answer_match in answer_matches:
            answer = normalize_space(answer_match.group(2))
            answer = re.sub(r"\s*고3 매일 학습지\s*\d+회차.*$", "", answer).strip()
            if answer:
                entries.append({"number": int(answer_match.group(1)), "answer": answer})
        entries.sort(key=lambda item: item["number"])
        if entries:
            output[category] = entries[:15]
    return output


def clean_explanation(value: str) -> str:
    value = normalize_space(value.replace("\n", " "))
    value = re.sub(r"고3 매일 학습지\s*\d+회차.*$", "", value).strip()
    return value or "해설이 등록되지 않았습니다."


def save_parsed_workbook(parsed: dict[int, dict[str, Any]]) -> dict[str, int]:
    question_count = 0
    for number, data in parsed.items():
        worksheet = Worksheet.query.filter_by(number=number).first()
        if not worksheet:
            worksheet = Worksheet(number=number, title=data["title"])
            db.session.add(worksheet)
        worksheet.title = data["title"]
        worksheet.subtitle = data["subtitle"]
        worksheet.recommended_minutes = data["recommended_minutes"]
        worksheet.original_questions = data["original_questions"]
        worksheet.transformed_questions = data["transformed_questions"]
        worksheet.concept_answers = data["concept_answers"]
        worksheet.imported_at = utcnow()
        worksheet.published = True
        question_count += len(data["original_questions"]) + len(data["transformed_questions"])
    db.session.flush()
    return {"worksheets": len(parsed), "questions": question_count}


app = create_app()

if __name__ == "__main__":
    app.run(host=os.getenv("HOST", "127.0.0.1"), port=int(os.getenv("PORT", "5000")), debug=True)
