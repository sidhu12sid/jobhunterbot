import urllib.parse
from typing import Any, Dict, Optional


def generate_email_draft(
    job: Dict[str, Any],
    profile_key: str,
    profile_data: Dict[str, Any],
    personal_info: Dict[str, Any]
) -> Dict[str, str]:
    """Generate personalized job application email draft with 1-click mailto link for direct HR application."""
    company = job.get("company", "Hiring Team")
    title = job.get("title", "Software Engineer")
    hr_email = job.get("hr_email") or ""

    first_name = personal_info.get("first_name", "Sidharth")
    last_name = personal_info.get("last_name", "Soman")
    full_name = f"{first_name} {last_name}".strip()
    phone = personal_info.get("phone", "+91-9645015897")
    email = personal_info.get("email", "sidharthsoman065@gmail.com")
    location = personal_info.get("location", "Calicut, Kerala, India")
    linkedin = personal_info.get("linkedin", "https://linkedin.com/in/sidharth-soman")
    github = personal_info.get("github", "https://github.com/sidhu12sid")
    website = personal_info.get("website", "https://sidharthsoman.online")

    resume_path = profile_data.get("resume_path", "assets/resume.pdf")
    resume_name = resume_path.replace("\\", "/").split("/")[-1]

    exp_tag = profile_data.get("subject_experience_tag")
    if not exp_tag:
        exp_tag = "4 Years Experience" if "dotnet" in profile_key.lower() else "1+ Year Experience"

    subject = f"Application for {title} - {full_name} ({exp_tag})"

    if "dotnet" in profile_key.lower():
        body = (
            f"Dear Hiring Team at {company},\n\n"
            f"I am writing to express my interest in the {title} role. "
            f"With 4 years of software development experience specializing in C#, .NET Core, and enterprise RESTful APIs, "
            f"I am confident in my ability to build scalable backend architectures and integrate modern responsive frontends.\n\n"
            f"Key technical highlights:\n"
            f"- 4 years of software development experience specializing in C#, .NET Core, and enterprise RESTful APIs.\n"
            f"- Scalable ASP.NET Core Web APIs, microservices, and Entity Framework Core / MS SQL Server.\n"
            f"- Single-page application development using Angular, TypeScript, and RxJS.\n"
            f"- Robust security with JWT, automated testing (xUnit), Azure App Services, and Git CI/CD.\n"
            f"- Technical portfolio and projects: {website}\n\n"
            f"I have attached my resume ({resume_name}) for your review. I look forward to speaking with you about "
            f"how my technical background can benefit the team at {company}.\n\n"
            f"Thank you for your time and consideration.\n\n"
            f"Warm regards,\n"
            f"{full_name}\n"
            f"{location}\n"
            f"Phone: {phone}\n"
            f"Email: {email}\n"
            f"LinkedIn: {linkedin}\n"
            f"GitHub: {github}\n"
            f"Portfolio: {website}"
        )
    else:  # Laravel Track
        body = (
            f"Dear Hiring Team at {company},\n\n"
            f"I am writing to express my interest in the {title} role. "
            f"With 1+ year of hands-on experience developing enterprise web applications with PHP and Laravel, "
            f"I am confident in my ability to write clean, maintainable backend code, optimize MySQL queries with Eloquent ORM, "
            f"and deliver robust RESTful APIs.\n\n"
            f"Key technical highlights:\n"
            f"- 1+ year of hands-on experience developing enterprise web applications with PHP and Laravel.\n"
            f"- Robust RESTful API design, middleware, and authentication workflows.\n"
            f"- Relational database schema design, indexing, and optimization in MySQL.\n"
            f"- Frontend integrations with React.js, Vue.js, and Tailwind CSS, plus Git and Docker workflows.\n"
            f"- Technical portfolio and live projects: {website}\n\n"
            f"I have attached my resume ({resume_name}) for your review. I would welcome the opportunity to discuss "
            f"how my technical skills can add immediate value to {company}.\n\n"
            f"Thank you for your time and consideration.\n\n"
            f"Warm regards,\n"
            f"{full_name}\n"
            f"{location}\n"
            f"Phone: {phone}\n"
            f"Email: {email}\n"
            f"LinkedIn: {linkedin}\n"
            f"GitHub: {github}\n"
            f"Portfolio: {website}"
        )

    # Encode mailto URL using quote
    query_params = urllib.parse.urlencode({
        "subject": subject,
        "body": body
    }, quote_via=urllib.parse.quote)
    mailto_url = f"mailto:{hr_email}?{query_params}" if hr_email else ""

    return {
        "to": hr_email,
        "subject": subject,
        "body": body,
        "mailto_url": mailto_url,
        "resume_name": resume_name,
        "resume_path": resume_path
    }
