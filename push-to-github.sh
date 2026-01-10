#!/bin/bash
# Script to push World Model MCP to GitHub

set -e  # Exit on error

echo "🚀 World Model MCP - GitHub Push Script"
echo "========================================"
echo ""

# Check if git is initialized
if [ ! -d ".git" ]; then
    echo "❌ Error: Not a git repository. Run 'git init' first."
    exit 1
fi

# Get GitHub username
echo "Please enter your GitHub username:"
read -r GITHUB_USERNAME

if [ -z "$GITHUB_USERNAME" ]; then
    echo "❌ Error: GitHub username is required"
    exit 1
fi

# Confirm repository name
REPO_NAME="world-model-mcp"
echo ""
echo "Repository name will be: $REPO_NAME"
echo "Full URL will be: https://github.com/$GITHUB_USERNAME/$REPO_NAME"
echo ""
echo "Is this correct? (y/n)"
read -r CONFIRM

if [ "$CONFIRM" != "y" ] && [ "$CONFIRM" != "Y" ]; then
    echo "Cancelled."
    exit 0
fi

# Check if remote already exists
if git remote | grep -q "^origin$"; then
    echo "⚠️  Remote 'origin' already exists. Removing it..."
    git remote remove origin
fi

# Add GitHub remote
echo ""
echo "📡 Adding GitHub remote..."
git remote add origin "https://github.com/$GITHUB_USERNAME/$REPO_NAME.git"

echo "✓ Remote added: https://github.com/$GITHUB_USERNAME/$REPO_NAME.git"
echo ""

# Remind user to create GitHub repository
echo "⚠️  IMPORTANT: Before pushing, make sure you've created the repository on GitHub:"
echo ""
echo "1. Go to: https://github.com/new"
echo "2. Repository name: $REPO_NAME"
echo "3. Set as Public"
echo "4. DO NOT initialize with README, .gitignore, or license"
echo "5. Click 'Create repository'"
echo ""
echo "Have you created the GitHub repository? (y/n)"
read -r CREATED

if [ "$CREATED" != "y" ] && [ "$CREATED" != "Y" ]; then
    echo ""
    echo "Please create the repository first, then run this script again."
    echo "Repository URL: https://github.com/new"
    exit 0
fi

# Push to GitHub
echo ""
echo "📤 Pushing to GitHub..."
git push -u origin main

echo ""
echo "✅ Successfully pushed to GitHub!"
echo ""
echo "🎉 Your repository is now live at:"
echo "   https://github.com/$GITHUB_USERNAME/$REPO_NAME"
echo ""
echo "📝 Next steps:"
echo "1. Update repository URLs in README.md and CONTRIBUTING.md"
echo "2. Add topics/tags to your repository"
echo "3. Enable Issues and Discussions"
echo "4. Create your first GitHub Release (v0.1.0)"
echo ""
echo "See GITHUB_SETUP.md for detailed instructions."
echo ""
