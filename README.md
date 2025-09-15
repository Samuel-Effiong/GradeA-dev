# Grade-Automator-Plus

A comprehensive automated grading system for educational assignments, powered by AI and OCR technologies.

![License](https://img.shields.io/badge/license-MIT-blue.svg)

## Table of Contents

- [Overview](#overview)
- [Features](#features)
- [Technologies Used](#technologies-used)
- [Project Structure](#project-structure)
- [Installation](#installation)
  - [Prerequisites](#prerequisites)
  - [Local Setup](#local-setup)
  - [Docker Setup](#docker-setup)
- [Environment Variables](#environment-variables)
- [Usage](#usage)
  - [API Endpoints](#api-endpoints)
  - [Creating Assignments](#creating-assignments)
  - [Grading Submissions](#grading-submissions)
- [API Documentation](#api-documentation)
- [Development](#development)
  - [Branching Strategy](#branching-strategy)
  - [Contributing](#contributing)
- [License](#license)

## Overview

Grade-Automator-Plus is an intelligent grading platform designed to streamline the assessment process for educators. The system leverages AI to automatically grade various types of assignments, extract information from PDFs, and provide detailed feedback to students.

The platform supports multiple assignment types (objective, essay, short answer, and hybrid) and uses sophisticated AI models to evaluate student submissions against customizable rubrics.

## Features

- **Assignment Management**: Create, update, and manage educational assignments
- **Intelligent Rubric Creation**: Define detailed grading criteria with scoring levels
- **PDF Processing**: Extract text and structure from PDF assignments using OCR
- **AI-Powered Grading**: Automatically grade submissions using AI models
- **Customizable Feedback**: Generate detailed, constructive feedback for students
- **User Management**: Support for different user roles (teachers, students, administrators)
- **Classroom Organization**: Group students and assignments into classrooms
- **RESTful API**: Comprehensive API for integration with other educational systems

## Technologies Used

- **Backend Framework**: Django 5.2.6 with Django REST Framework
- **Database**: PostgreSQL (via dj-database-url)
- **Authentication**: JWT (JSON Web Tokens)
- **AI Integration**: OpenAI API
- **OCR Processing**: PaddleOCR and Pytesseract
- **PDF Processing**: PyMuPDF, pdf2image
- **API Documentation**: drf-spectacular
- **Containerization**: Docker
- **Email Service**: Sendinblue

## Project Structure

The project is organized into several Django apps, each responsible for specific functionality:

- **assignments**: Manages assignment creation, storage, and retrieval
- **ai_processor**: Handles AI integration, OCR, and PDF processing
- **classrooms**: Organizes students and assignments into classroom groups
- **grading**: Manages the grading process and feedback generation
- **students**: Handles student information and submissions
- **users**: Manages user authentication and permissions

## Installation

### Prerequisites

- Python 3.12+
- PostgreSQL
- OpenAI API key
- Docker (optional, for containerized deployment)

### Local Setup

1. Clone the repository:
   ```bash
   git clone https://github.com/yourusername/Grade-Automator-Plus.git
   cd Grade-Automator-Plus
   ```

2. Create and activate a virtual environment:
   ```bash
   python -m venv venv
   source venv/bin/activate  # On Windows: venv\Scripts\activate
   ```

3. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```

4. Create a `.env` file in the project root with the required environment variables (see [Environment Variables](#environment-variables))

5. Run migrations:
   ```bash
   python manage.py migrate
   ```

6. Create a superuser:
   ```bash
   python manage.py createsuperuser
   ```

7. Start the development server:
   ```bash
   python manage.py runserver
   ```

### Docker Setup

1. Build the Docker image:
   ```bash
   docker build -t grade-automator-plus .
   ```

2. Run the container:
   ```bash
   docker run -p 8000:8000 -e SECRET_KEY=your_secret_key -e DATABASE_URI=your_database_uri grade-automator-plus
   ```

## Environment Variables

Create a `.env` file in the project root with the following variables:


```
SECRET_KEY=your_django_secret_key
DATABASE_URI=your_database_uri
FRONTEND_DOMAIN=http://localhost:3000
OPENAI_API_KEY=your_openai_api_key
SENDINBLUE_API_KEY=your_sendinblue_api_key
```

## Usage

### API Endpoints

The system provides a RESTful API for all operations. Key endpoints include:

- `/api/assignments/`: Manage assignments
- `/api/rubrics/`: Create and manage grading rubrics
- `/api/classrooms/`: Organize students and assignments
- `/api/submissions/`: Handle student submissions
- `/api/grades/`: Access grading results

### Creating Assignments

1. Log in to the system
2. Navigate to the Assignments section
3. Click "Create New Assignment"
4. Fill in the assignment details (title, subject, instructions, etc.)
5. Upload any supporting documents
6. Define the grading rubric with criteria and scoring levels
7. Save and publish the assignment

### Grading Submissions

1. Access the submissions for a specific assignment
2. The system will automatically process and grade submissions based on the defined rubric
3. Review the AI-generated grades and feedback
4. Make any necessary adjustments
5. Approve and release grades to students

## API Documentation

The API is documented using drf-spectacular. You can access the interactive API documentation at:

- Swagger UI: `/api/schema/swagger-ui/`
- ReDoc: `/api/schema/redoc/`

## Development

### Branching Strategy

We follow a modified GitFlow approach for development. See [BRANCHING_STRATEGY.md](BRANCHING_STRATEGY.md) for detailed information.

Key branches:
- `main`: Production-ready code
- `develop`: Integration branch for features
- `feature/*`: For developing new features
- `bugfix/*`: For fixing bugs in development
- `hotfix/*`: For urgent production fixes
- `release/*`: For preparing new production releases

### Contributing

1. Fork the repository
2. Create a new branch following our [branching strategy](#branching-strategy)
3. Make your changes
4. Write or update tests as needed
5. Ensure all tests pass
6. Submit a pull request to the `develop` branch

Please follow our commit message format based on [Conventional Commits](https://www.conventionalcommits.org/).

## License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.

Copyright (c) 2025 Emanprojectz
