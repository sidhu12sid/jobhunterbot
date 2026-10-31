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

    experience_years = profile_data.get("experience_years", 1)
    resume_path = profile_data.get("resume_path", "assets/resume.pdf")
    resume_name = resume_path.replace("\\", "/").split("/")[-1]

    exp_label = f"{experience_years}+ Year{'s' if experience_years != 1 else ''}"
    subject = f"Application for {title} - {full_name} ({exp_label} Experience)"

    if "dotnet" in profile_key.lower():
        body = (
            f"Dear Hiring Team at {company},\n\n"
            f"I am writing to express my strong interest in the {title} opening at {company}.\n\n"
            f"With {exp_label} of software engineering experience specializing in C# and .NET Core RESTful APIs "
            f"paired with modern Angular frontends, I have proven experience developing scalable enterprise web services, "
            f"designing relational schemas with Entity Framework Core and MS SQL Server, and writing clean, test-driven code.\n\n"
            f"Key technical highlights:\n"
            f"- Scalable backend architecture using ASP.NET Core Web APIs, Dapper, and EF Core.\n"
            f"- Responsive single-page applications with Angular, TypeScript, and RxJS.\n"
            f"- Robust authentication with JWT and automated unit testing with xUnit.\n"
            f"- Technical portfolio and projects: {website}\n\n"
            f"I have attached my resume ({resume_name}) for your review. I would welcome the opportunity to discuss "
            f"how my technical background and problem-solving skills align with your engineering goals at {company}.\n\n"
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
            f"I am writing to express my strong interest in the {title} role at {company}.\n\n"
            f"With {exp_label} of software development experience specializing in PHP and the Laravel framework, "
            f"I have hands-on experience building secure RESTful APIs, optimizing complex database queries with MySQL "
            f"and Eloquent ORM, implementing Redis caching, and integrating responsive web interfaces.\n\n"
            f"Key technical highlights:\n"
            f"- Architecting modular, high-performance web applications using Laravel and PHP.\n"
            f"- Relational schema design, query optimization, and indexing in MySQL.\n"
            f"- Modern frontend integration (React.js, Vue.js, Tailwind CSS) and Docker workflows.\n"
            f"- Technical portfolio and live demos: {website}\n\n"
            f"I have attached my resume ({resume_name}) for your review. I look forward to speaking with you "
            f"about how I can contribute to {company}.\n\n"
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
