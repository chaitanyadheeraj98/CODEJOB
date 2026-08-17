from __future__ import annotations


REAL_NVOIDS_SIX_ROLE_TITLES = (
    "Databricks Developer (Healthcare domain, HL7 FHIR)",
    "AWS Cloud Security Engineer",
    "Data Modeling LEAD / Architect",
    "ServiceNow Discovery & Service Mapping Lead",
    "Camunda & Java Spring Boot Developer",
    "SAP BASIS Admin",
)

REAL_NVOIDS_SIX_ROLE_SOURCE = """From:
Recruiting Team
Hi,
Please review the requirements below and share the best relevant candidates you have.
Please join our recruiter group to stay updated on the latest requirements.
Role NAME |
Location |
YOE |
Rate (c2c) |
Databricks Developer (Healthcare domain, HL7 FHIR) |
Bloomfield, CT (Remote / Hybrid) |
7+ Years |
$60/hr C2C |
AWS Cloud Security Engineer |
San Antonio, TX |
8+ Years |
$50/hr C2C |
Data Modeling LEAD / Architect |
Mount Laurel, New Jersey (Local Only) |
12+ Years |
$55.00 - $60.00/hr C2C |
ServiceNow Discovery & Service Mapping Lead |
Philadelphia, PA / Mount Laurel, NJ / Wilmington, DE |
10+ Years |
$60/hr C2C |
Camunda & Java Spring Boot Developer |
Mt. Laurel, NJ (Local Only) |
8+ Years |
$55.00 - $60.00/hr C2C |
SAP BASIS Admin |
Jersey City, NJ |
12+ Years |
$65/hr C2C |
Detailed JDs can be found below
Databricks Developer (Healthcare domain, HL7 FHIR)
Location: Bloomfield, CT (Remote / Hybrid)
YOE: 7+ Years
Any Special Requests: Healthcare domain and HL7 FHIR experience is strongly preferred.
Important Skill-Set: Databricks, Python, PySpark, Apache Spark, Delta Lake, AWS.
About the Role: Design scalable healthcare data pipelines on Databricks and AWS.
AWS Cloud Security Engineer
Location: San Antonio, TX
YOE: 8+ Years
Any Special Requests: Must be willing to work 100% onsite in San Antonio, TX.
Important Skill-Set: AWS EKS, Kubernetes RBAC, Python, Git, CI/CD, SAST, DAST.
About the Role: Harden AWS environments and automate cloud threat hunting.
Data Modeling LEAD / Architect
Location: Mount Laurel, New Jersey (Local Only)
YOE: 12+ Years
Any Special Requests: Local candidates to Mount Laurel, New Jersey only.
Important Skill-Set: Conceptual, logical, and physical data modeling, governance, RDBMS, NoSQL.
About the Role: Lead enterprise data architecture and authoritative data definitions.
ServiceNow Discovery & Service Mapping Lead
Location: Philadelphia, PA / Mount Laurel, NJ / Wilmington, DE
YOE: 10+ Years
Any Special Requests: Onsite role.
Important Skill-Set: ServiceNow ITOM, Discovery, Service Mapping, CMDB, MID Server.
About the Role: Lead ServiceNow discovery and service-mapping delivery.
Camunda & Java Spring Boot Developer
Location: Mt. Laurel, NJ (Local Only)
YOE: 8+ Years
Any Special Requests: Local candidates only.
Important Skill-Set: Camunda, Java, Spring Boot, BPMN, REST APIs, microservices.
About the Role: Build workflow automation services with Camunda and Spring Boot.
SAP BASIS Admin
Location: Jersey City, NJ
YOE: 12+ Years
Any Special Requests: Onsite availability is required.
Important Skill-Set: SAP BASIS, HANA, S/4HANA, system monitoring, upgrades.
About the Role: Administer and optimize enterprise SAP systems.
Please send sanitized candidate profiles to the recruiting team.
Keywords: healthcare cloud security data modeling ServiceNow Camunda SAP"""


REAL_NVOIDS_THREE_ROLE_TITLES = (
    "DevOps Engineer - Ansible",
    "Java Full Stack Developer - Gen AI / LLM / ADK",
    "Java Full Stack Developer - Heavy UI",
)

REAL_NVOIDS_THREE_ROLE_SOURCE = """Hi #Connections,
EXCITING C2C JOB OPPORTUNITIES - IMMEDIATE REQUIREMENTS!
We are actively looking for qualified and local candidates for the following onsite opportunities.
1. DevOps Engineer - Ansible
Charlotte, NC | Onsite
C2C
Local Candidates Only - North Carolina, NC
Key Skills:
CI/CD Pipelines
Ansible
Python Scripting
Database Deployment & Migration - Liquibase
SQL & Database Performance Basics
Git / Version Control
Environment & Configuration Management
2. Java Full Stack Developer - Gen AI / LLM / ADK
Columbus, OH | Onsite
C2C
Local Candidates Only - Ohio, OH
Must-Have Skills:
Java, Spring & Spring Boot
React, TypeScript, JavaScript
Next.js, Tailwind, Bootstrap
REST & GraphQL APIs
Microservices Architecture
Generative AI / LLM
ADK / OpenAI Agent Frameworks
LangChain / LangGraph
Prompt Engineering & RAG
Git / CI/CD
AWS / Azure / GCP
Kafka / Redis
7+ Years of Software Development Experience
2+ Years of Hands-on Generative AI Experience
3. Java Full Stack Developer - Heavy UI
Chandler, AZ | Onsite
C2C
Local Candidates Only - Arizona, AZ
Must-Have Skills:
Java - 5+ Years
React / TypeScript / JavaScript - 5+ Years
Redux Toolkit & React Hook Form
Enterprise React Ecosystems
Design Systems / Component Libraries
Fluent UI / Material UI / Storybook
Micro Frontend Architecture
Module Federation & Monorepos
REST / SOAP / JSON APIs
Spring / Spring Boot
CI/CD & DevOps
AI / Gen AI / Agentic Automation
Web Application Security
60-70% UI / Frontend-focused development
Kindly share an updated resume with the recruiting team.
Keywords: DevOps Ansible Java Full Stack Generative AI user interface"""


def manifest_payload_for(source: str, titles: tuple[str, ...], *, detail_marker: str | None = None) -> dict[str, object]:
    lines = source.splitlines()
    search_start = lines.index(detail_marker) + 1 if detail_marker else 0
    starts = [
        next(
            index
            for index, line in enumerate(lines, start=1)
            if index > search_start and (line == title or line.endswith(title))
        )
        for title in titles
    ]
    footer_start = next(
        (
            index
            for index, line in enumerate(lines, start=1)
            if index > starts[-1] and (line.startswith("Please send") or line.startswith("Kindly share"))
        ),
        len(lines) + 1,
    )
    roles = []
    for index, (title, start_line) in enumerate(zip(titles, starts, strict=True), start=1):
        end_line = starts[index] - 1 if index < len(starts) else footer_start - 1
        roles.append(
            {
                "index": index,
                "title_hint": title,
                "requisition_id": "",
                "start_line": start_line,
                "end_line": end_line,
                "confidence": 0.97,
            }
        )
    return {
        "classification": "multiple",
        "role_count": len(roles),
        "confidence": 0.96,
        "shared_constraints": [],
        "roles": roles,
    }


REAL_NVOIDS_SIX_ROLE_MANIFEST = manifest_payload_for(
    REAL_NVOIDS_SIX_ROLE_SOURCE,
    REAL_NVOIDS_SIX_ROLE_TITLES,
    detail_marker="Detailed JDs can be found below",
)
REAL_NVOIDS_THREE_ROLE_MANIFEST = manifest_payload_for(
    REAL_NVOIDS_THREE_ROLE_SOURCE,
    REAL_NVOIDS_THREE_ROLE_TITLES,
)
