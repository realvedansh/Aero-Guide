"""
services/education_service.py
Read/write access to the Class 6-12 curriculum database (classes, subjects,
chapters, study notes, questions). Kept separate from routes so the schema
can be reused by /ask, /generate_quiz, /get_notes, and any future admin tools.
"""

import logging
from sqlalchemy.exc import IntegrityError
from slugify_util import slugify

from extensions import db
from models import EducationClass, Subject, Chapter, StudyNote, Question

logger = logging.getLogger("aeroguide")


def list_classes():
    return EducationClass.query.order_by(EducationClass.grade).all()


def get_class(grade: int):
    return EducationClass.query.filter_by(grade=grade).first()


def list_subjects(grade: int):
    cls = get_class(grade)
    if not cls:
        return []
    return cls.subjects.order_by(Subject.name).all()


def get_subject(grade: int, subject_slug: str):
    cls = get_class(grade)
    if not cls:
        return None
    return cls.subjects.filter_by(slug=subject_slug).first()


def list_chapters(grade: int, subject_slug: str):
    subject = get_subject(grade, subject_slug)
    if not subject:
        return []
    return subject.chapters.order_by(Chapter.order_index).all()


def get_chapter(grade: int, subject_slug: str, chapter_slug: str):
    subject = get_subject(grade, subject_slug)
    if not subject:
        return None
    return subject.chapters.filter_by(slug=chapter_slug).first()


def search_chapters(query: str, limit: int = 20):
    """Simple case-insensitive search across chapter names/summaries."""
    like = f"%{query.strip()}%"
    return (
        Chapter.query.filter(
            db.or_(Chapter.name.ilike(like), Chapter.summary.ilike(like))
        )
        .limit(limit)
        .all()
    )


# --- Admin write helpers (used by the protected /admin/content endpoints) ---

def ensure_class(grade: int) -> EducationClass:
    cls = get_class(grade)
    if cls:
        return cls
    cls = EducationClass(grade=grade, display_name=f"Class {grade}")
    db.session.add(cls)
    try:
        db.session.commit()
    except IntegrityError:
        # Another concurrent request created it first — that's fine, use theirs.
        db.session.rollback()
        cls = get_class(grade)
    return cls


def upsert_subject(grade: int, name: str) -> Subject:
    cls = ensure_class(grade)
    slug = slugify(name)
    subject = cls.subjects.filter_by(slug=slug).first()
    if subject:
        return subject
    subject = Subject(class_id=cls.id, name=name, slug=slug)
    db.session.add(subject)
    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        subject = cls.subjects.filter_by(slug=slug).first()
    return subject


def upsert_chapter(grade: int, subject_name: str, chapter_name: str,
                    summary: str = "", order_index: int = 0) -> Chapter:
    subject = upsert_subject(grade, subject_name)
    slug = slugify(chapter_name)
    chapter = subject.chapters.filter_by(slug=slug).first()
    if chapter:
        return chapter
    chapter = Chapter(
        subject_id=subject.id, name=chapter_name, slug=slug,
        summary=summary, order_index=order_index,
    )
    db.session.add(chapter)
    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        chapter = subject.chapters.filter_by(slug=slug).first()
    return chapter


def add_note(chapter: Chapter, title: str, content_markdown: str) -> StudyNote:
    note = StudyNote(chapter_id=chapter.id, title=title, content_markdown=content_markdown)
    db.session.add(note)
    db.session.commit()
    return note


def add_question(chapter: Chapter, q_type: str, question_text: str,
                  correct_answer: str, options=None, explanation: str = "",
                  difficulty: str = "medium") -> Question:
    question = Question(
        chapter_id=chapter.id, q_type=q_type, question_text=question_text,
        options=options, correct_answer=correct_answer,
        explanation=explanation, difficulty=difficulty,
    )
    db.session.add(question)
    db.session.commit()
    return question
