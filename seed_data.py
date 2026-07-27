"""
seed_data.py

Populates the Class 6-12 curriculum skeleton (all classes + their standard
subject lists) plus a small set of fully-worked example chapters (notes +
questions) so the schema and API can be demoed and tested end to end.

IMPORTANT / HONESTY NOTE:
A complete, accurate Class 6-12 content database (every chapter of every
NCERT/state-board subject, with vetted notes and questions) is a genuinely
large content project — realistically thousands of chapters across ~8
subjects x 7 grades, each needing accurate, board-aligned material. That
can't be responsibly fabricated in one script. This seed file instead:
  1. Creates the full grade/subject skeleton (so the API surface is complete
     and correct for all Classes 6-12 immediately), and
  2. Adds a handful of fully worked example chapters with real notes and
     questions, so you can see the exact JSON shape to bulk-import the rest.

Run with:  python seed_data.py
Bulk import your own content by POSTing to /admin/content/chapter (see
README.md) or by extending EXAMPLE_CHAPTERS below and re-running this script
(it's idempotent — re-running won't create duplicates).
"""

import logging

from main import create_app
from services.education_service import ensure_class, upsert_subject, upsert_chapter, upsert_note as add_note, upsert_question as add_question

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("aeroguide.seed")

STANDARD_SUBJECTS_BY_GRADE = {
    6: ["Mathematics", "Science", "Social Science", "English", "Hindi"],
    7: ["Mathematics", "Science", "Social Science", "English", "Hindi"],
    8: ["Mathematics", "Science", "Social Science", "English", "Hindi"],
    9: ["Mathematics", "Science", "Social Science", "English"],
    10: ["Mathematics", "Science", "Social Science", "English"],
    11: ["Physics", "Chemistry", "Mathematics", "Biology", "Computer Science", "English"],
    12: ["Physics", "Chemistry", "Mathematics", "Biology", "Computer Science", "English"],
}

# A small number of fully worked examples showing the intended data shape.
EXAMPLE_CHAPTERS = [
    {
        "grade": 10, "subject": "Science", "chapter": "Light - Reflection and Refraction",
        "summary": "Laws of reflection/refraction, mirrors, lenses, and the human eye.",
        "notes": [
            {
                "title": "Quick Revision: Light",
                "content": (
                    "- Laws of reflection: angle of incidence = angle of reflection; "
                    "incident ray, normal, reflected ray lie in the same plane.\n"
                    "- Mirror formula: 1/v + 1/u = 1/f\n"
                    "- Magnification: m = -v/u = h'/h\n"
                    "- Lens formula: 1/v - 1/u = 1/f\n"
                    "- Power of lens: P = 1/f (f in metres), unit dioptre (D)\n"
                    "- Convex lens/mirror: converging; Concave: diverging (mirror), "
                    "converging (lens works opposite of mirror sign convention)."
                ),
            }
        ],
        "questions": [
            {
                "q_type": "mcq",
                "question_text": "The power of a lens of focal length 50 cm is:",
                "options": ["+0.5 D", "+2 D", "-2 D", "+5 D"],
                "correct_answer": "+2 D",
                "explanation": "P = 1/f = 1/0.5 m = 2 D. Convex (converging) lens has positive focal length.",
                "difficulty": "easy",
            },
        ],
    },
    {
        "grade": 12, "subject": "Physics", "chapter": "Electrostatics",
        "summary": "Coulomb's law, electric field, potential, capacitance, Gauss's law.",
        "notes": [
            {
                "title": "Quick Revision: Electrostatics",
                "content": (
                    "- Coulomb's law: F = k q1 q2 / r^2, k = 1/(4*pi*epsilon0) ~ 9x10^9 N m^2/C^2\n"
                    "- Electric field: E = F/q = k Q / r^2\n"
                    "- Electric potential: V = k Q / r\n"
                    "- Gauss's law: flux = Q_enclosed / epsilon0\n"
                    "- Capacitance of parallel plate capacitor: C = epsilon0 * A / d\n"
                    "- Energy stored in capacitor: U = 1/2 C V^2"
                ),
            }
        ],
        "questions": [
            {
                "q_type": "short",
                "question_text": "State Gauss's law and give one application.",
                "correct_answer": (
                    "Total electric flux through a closed surface = Q_enclosed / epsilon0. "
                    "Application: finding the electric field of a uniformly charged infinite "
                    "sheet or sphere quickly, without integrating Coulomb's law directly."
                ),
                "explanation": "",
                "difficulty": "medium",
            },
        ],
    },
    {
        "grade": 12, "subject": "Computer Science", "chapter": "Python Functions",
        "summary": "Defining functions, parameters, default arguments, scope, recursion.",
        "notes": [
            {
                "title": "Quick Revision: Python Functions",
                "content": (
                    "- def keyword defines a function; return sends a value back.\n"
                    "- Default arguments: def f(x, y=10): — y is optional.\n"
                    "- *args collects extra positional args as a tuple; **kwargs as a dict.\n"
                    "- Variable scope: local variables exist only inside the function; "
                    "use 'global' to modify a global variable inside a function.\n"
                    "- Recursion: a function calling itself; must have a base case to stop."
                ),
            }
        ],
        "questions": [
            {
                "q_type": "mcq",
                "question_text": "Which keyword is used to modify a global variable inside a function?",
                "options": ["local", "global", "nonlocal", "static"],
                "correct_answer": "global",
                "explanation": "'global' tells Python the assignment inside the function should affect the module-level variable.",
                "difficulty": "easy",
            },
        ],
    },
]


def seed():
    for grade, subjects in STANDARD_SUBJECTS_BY_GRADE.items():
        ensure_class(grade)
        for subject_name in subjects:
            upsert_subject(grade, subject_name)
        logger.info("Class %d skeleton ready with %d subjects.", grade, len(subjects))

    for item in EXAMPLE_CHAPTERS:
        chapter = upsert_chapter(
            grade=item["grade"],
            subject_name=item["subject"],
            chapter_name=item["chapter"],
            summary=item.get("summary", ""),
        )
        for note in item.get("notes", []):
            add_note(chapter, note["title"], note["content"])
        for q in item.get("questions", []):
            add_question(
                chapter,
                q_type=q["q_type"],
                question_text=q["question_text"],
                correct_answer=q["correct_answer"],
                options=q.get("options"),
                explanation=q.get("explanation", ""),
                difficulty=q.get("difficulty", "medium"),
            )
        logger.info("Seeded example chapter: Class %d %s / %s", item["grade"], item["subject"], item["chapter"])

    logger.info("Seeding complete.")


if __name__ == "__main__":
    app = create_app()
    with app.app_context():
        seed()