import json
from datetime import datetime
from sqlalchemy import create_engine, Column, String, Text, Float, DateTime, Boolean
from sqlalchemy.orm import declarative_base, sessionmaker
from app.config import DATABASE_URL
from app.logger import logger

Base = declarative_base()

class Job(Base):
    __tablename__ = 'jobs'
    
    id = Column(String, primary_key=True)
    title = Column(String, nullable=False)
    description = Column(Text, nullable=True)
    url = Column(String, nullable=False)
    job_type = Column(String, nullable=True)
    budget = Column(String, nullable=True)
    experience_level = Column(String, nullable=True)
    skills = Column(Text, nullable=True)
    client_country = Column(String, nullable=True)
    client_spent = Column(Float, nullable=True)
    client_rating = Column(Float, nullable=True)
    published_date = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    posted_to_discord = Column(Boolean, default=False)

    def to_dict(self):
        return {
            "id": self.id,
            "title": self.title,
            "description": self.description,
            "url": self.url,
            "job_type": self.job_type,
            "budget": self.budget,
            "experience_level": self.experience_level,
            "skills": json.loads(self.skills) if self.skills else [],
            "client_country": self.client_country,
            "client_spent": self.client_spent,
            "client_rating": self.client_rating,
            "published_date": self.published_date.isoformat() if self.published_date else None,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "posted_to_discord": self.posted_to_discord
        }

engine = create_engine(
    DATABASE_URL, 
    connect_args={"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

def init_db():
    logger.info("Initializing database and tables...")
    try:
        Base.metadata.create_all(bind=engine)
        logger.info("Database initialized successfully.")
    except Exception as e:
        logger.error(f"Error initializing database: {e}")
        raise

class DBManager:
    @staticmethod
    def add_job(job_data: dict) -> bool:
        session = SessionLocal()
        try:
            exists = session.query(Job).filter(Job.id == job_data["id"]).first()
            if exists:
                return False
            
            skills_raw = job_data.get("skills", [])
            skills_str = json.dumps(skills_raw) if isinstance(skills_raw, list) else str(skills_raw)
            
            pub_date = job_data.get("published_date")
            if isinstance(pub_date, str):
                try:
                    pub_date = datetime.fromisoformat(pub_date.replace("Z", "+00:00"))
                except ValueError:
                    pub_date = datetime.utcnow()
            elif not pub_date:
                pub_date = datetime.utcnow()

            job = Job(
                id=job_data["id"],
                title=job_data["title"],
                description=job_data.get("description", ""),
                url=job_data["url"],
                job_type=job_data.get("job_type"),
                budget=job_data.get("budget"),
                experience_level=job_data.get("experience_level"),
                skills=skills_str,
                client_country=job_data.get("client_country"),
                client_spent=job_data.get("client_spent"),
                client_rating=job_data.get("client_rating"),
                published_date=pub_date
            )
            session.add(job)
            session.commit()
            logger.info(f"Saved new job: {job.title} ({job.id})")
            return True
        except Exception as e:
            session.rollback()
            logger.error(f"Error adding job to database: {e}")
            return False
        finally:
            session.close()

    @staticmethod
    def is_duplicate(job_id: str) -> bool:
        session = SessionLocal()
        try:
            exists = session.query(Job).filter(Job.id == job_id).first()
            return exists is not None
        finally:
            session.close()

    @staticmethod
    def get_unposted_jobs() -> list[Job]:
        session = SessionLocal()
        try:
            jobs = session.query(Job).filter(Job.posted_to_discord == False).order_by(Job.published_date.asc()).all()
            session.expunge_all()
            return jobs
        finally:
            session.close()

    @staticmethod
    def mark_as_posted(job_id: str) -> bool:
        session = SessionLocal()
        try:
            job = session.query(Job).filter(Job.id == job_id).first()
            if job:
                job.posted_to_discord = True
                session.commit()
                return True
            return False
        except Exception as e:
            session.rollback()
            logger.error(f"Error marking job as posted: {e}")
            return False
        finally:
            session.close()
