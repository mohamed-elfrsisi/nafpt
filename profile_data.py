"""Structured facts used by the classifier and for validating AI output.
Keep in sync with profile.md (a test checks every project name appears there)."""

DISPLAY_NAME_AR = "محمد ياسر الفرسيسي"
DISPLAY_NAME_EN = "Mohamed Yasser Elfarsisi"

CATEGORY_LABELS = {
    "dotnet": ".NET Backend",
    "mean": "MEAN Stack",
    "laravel": "Laravel / PHP",
    "ai": "AI Application Integration",
    "general_backend": "General Backend",
    "database": "Database",
    "api_integration": "API Integration",
    "fullstack": "Full-Stack Web",
}
PRIORITY = {"dotnet": 1, "mean": 2, "laravel": 3, "ai": 4,
            "general_backend": 5, "database": 6, "api_integration": 7, "fullstack": 8}

# name -> short honest description + project type (do not exaggerate)
VERIFIED_PROJECTS = {
    "NTI Career Platform": "Laravel/PHP/MySQL/Bootstrap, team project (training program), I led the frontend of a 3-member team and worked on 63 REST endpoints",
    "ERP System": "Angular 16, Node.js, Express, MongoDB, 4-member team project: HR, Sales, Inventory, 15 CRUD modules, 83 REST endpoints",
    "FocusFlow": "React, ASP.NET Core Web API, SQL Server, DEPI final team project built from BRD/FSD with Agile sprints",
    "Clario": "Python, PostgreSQL, NLP, embeddings: AI career intelligence platform (resume parsing, fit evaluation, skill-gap analysis)",
    "NASA Space Habitat": "PHP, Python, AI, NASA public APIs: Space Apps Challenge 2025 prototype for Moon/Mars habitat planning",
}
