# Contributing to MyKeep

Thank you for your interest in improving MyKeep!

## Getting Started

1. Fork and clone the repository.
2. Create and activate a Python virtual environment:
   ```bash
   python3 -m venv venv
   source venv/bin/activate
   ```
3. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```
4. Copy environment configuration and apply migrations:
   ```bash
   cp .env.example .env
   python manage.py migrate
   ```

## Development Workflow

1. Create a feature or bugfix branch from `main`:
   ```bash
   git checkout -b feature/your-feature-name
   ```
2. Make your changes adhering to clean code and existing conventions.
3. Run test suite to ensure all tests pass:
   ```bash
   python manage.py test
   ```
4. Commit your changes with clear, descriptive commit messages.
5. Push to your branch and open a Pull Request.

## Pull Request Guidelines

- Ensure your branch is up to date with `main`.
- Add tests for any new features or bug fixes.
- Keep PRs focused on a single topic or fix.
