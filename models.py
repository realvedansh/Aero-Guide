"""
models.py
All persistent data lives here:
  - ChatMessage: per-session chat history
  - KnowledgeEntry: the adaptive-learning memory (question -> learned answer)
  - EducationClass / Subject / Chapter / StudyNote / Question: the Class 6-12
    curriculum database
"""

import datetime
import uuid

from extensions import db


def _uuid():
    return str(uuid.uuid4())


class ChatMessage(db.Model):
    __tablename__ = "chat_messages"

    id = db.Column(db.Integer, primary_key=True)
    session_id = db.Column(db.String(64), index=True, nullable=False)
    role = db.Column(db.String(16), nullable=False)  # user | assistant | system
    content = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.datetime.utcnow, index=True)

    def to_dict(self):
        return {"role": self.role, "content": self.content}


class KnowledgeEntry(db.Model):
    """
    The adaptive-learning store. When the AI is asked something it doesn't
    already know a good answer to, the resolved answer (after web search / a
    model call) is written here, keyed by a normalized version of the
    question. Future similar questions are served from here first, which both
    saves API cost/latency and lets the assistant "remember" what it learned.
    """

    __tablename__ = "knowledge_entries"

    id = db.Column(db.Integer, primary_key=True)
    entry_uuid = db.Column(db.String(36), unique=True, default=_uuid)
    normalized_question = db.Column(db.Text, nullable=False, index=True)
    original_question = db.Column(db.Text, nullable=False)
    answer = db.Column(db.Text, nullable=False)
    source = db.Column(db.String(32), default="model")  # model | web_search | admin
    # Unverified entries came straight from a model/web search and are served
    # with a lower-confidence framing; admins can promote them to verified.
    verified = db.Column(db.Boolean, default=False)
    hit_count = db.Column(db.Integer, default=0)
    created_at = db.Column(db.DateTime, default=datetime.datetime.utcnow)
    last_used_at = db.Column(db.DateTime, default=datetime.datetime.utcnow)

    def to_dict(self):
        return {
            "id": self.entry_uuid,
            "question": self.original_question,
            "answer": self.answer,
            "source": self.source,
            "verified": self.verified,
            "hit_count": self.hit_count,
        }


class EducationClass(db.Model):
    """Class 6 through Class 12."""

    __tablename__ = "education_classes"

    id = db.Column(db.Integer, primary_key=True)
    grade = db.Column(db.Integer, unique=True, nullable=False)  # 6..12
    display_name = db.Column(db.String(32), nullable=False)  # "Class 6"

    subjects = db.relationship(
        "Subject", backref="education_class", lazy="dynamic",
        cascade="all, delete-orphan"
    )

    def to_dict(self):
        return {"grade": self.grade, "name": self.display_name}


class Subject(db.Model):
    __tablename__ = "subjects"
    __table_args__ = (
        db.UniqueConstraint("class_id", "slug", name="uq_subject_per_class"),
    )

    id = db.Column(db.Integer, primary_key=True)
    class_id = db.Column(db.Integer, db.ForeignKey("education_classes.id"), nullable=False)
    name = db.Column(db.String(128), nullable=False)
    slug = db.Column(db.String(128), nullable=False, index=True)

    chapters = db.relationship(
        "Chapter", backref="subject", lazy="dynamic",
        cascade="all, delete-orphan"
    )

    def to_dict(self, include_chapters=False):
        data = {"id": self.id, "name": self.name, "slug": self.slug}
        if include_chapters:
            data["chapters"] = [c.to_dict() for c in self.chapters]
        return data


class Chapter(db.Model):
    __tablename__ = "chapters"
    __table_args__ = (
        db.UniqueConstraint("subject_id", "slug", name="uq_chapter_per_subject"),
    )

    id = db.Column(db.Integer, primary_key=True)
    subject_id = db.Column(db.Integer, db.ForeignKey("subjects.id"), nullable=False)
    order_index = db.Column(db.Integer, default=0)
    name = db.Column(db.String(256), nullable=False)
    slug = db.Column(db.String(256), nullable=False, index=True)
    summary = db.Column(db.Text, default="")

    notes = db.relationship(
        "StudyNote", backref="chapter", lazy="dynamic",
        cascade="all, delete-orphan"
    )
    questions = db.relationship(
        "Question", backref="chapter", lazy="dynamic",
        cascade="all, delete-orphan"
    )

    def to_dict(self, include_children=False):
        data = {
            "id": self.id,
            "name": self.name,
            "slug": self.slug,
            "order": self.order_index,
            "summary": self.summary,
        }
        if include_children:
            data["notes"] = [n.to_dict() for n in self.notes]
            data["questions"] = [q.to_dict() for q in self.questions]
        return data


class StudyNote(db.Model):
    __tablename__ = "study_notes"

    id = db.Column(db.Integer, primary_key=True)
    chapter_id = db.Column(db.Integer, db.ForeignKey("chapters.id"), nullable=False)
    title = db.Column(db.String(256), nullable=False)
    content_markdown = db.Column(db.Text, nullable=False)

    def to_dict(self):
        return {"id": self.id, "title": self.title, "content": self.content_markdown}


class Question(db.Model):
    __tablename__ = "questions"

    QUESTION_TYPES = ("mcq", "short", "long")

    id = db.Column(db.Integer, primary_key=True)
    chapter_id = db.Column(db.Integer, db.ForeignKey("chapters.id"), nullable=False)
    q_type = db.Column(db.String(16), nullable=False, default="mcq")
    question_text = db.Column(db.Text, nullable=False)
    options = db.Column(db.JSON, nullable=True)  # ["A text", "B text", ...] for mcq
    correct_answer = db.Column(db.Text, nullable=False)
    explanation = db.Column(db.Text, default="")
    difficulty = db.Column(db.String(16), default="medium")  # easy|medium|hard

    def to_dict(self, reveal_answer=True):
        data = {
            "id": self.id,
            "type": self.q_type,
            "question": self.question_text,
            "options": self.options,
            "difficulty": self.difficulty,
        }
        if reveal_answer:
            data["correct_answer"] = self.correct_answer
            data["explanation"] = self.explanation
        return data