#!/bin/bash
# Fix stale git branch configuration

set -e

echo "🔧 Fixing git configuration..."

# Get current branch
CURRENT_BRANCH=$(git branch --show-current)
echo "Current branch: $CURRENT_BRANCH"

# Check if branch has stale tracking configuration
MERGE_REF=$(git config --get branch.$CURRENT_BRANCH.merge 2>/dev/null || echo "")
REMOTE=$(git config --get branch.$CURRENT_BRANCH.remote 2>/dev/null || echo "")

if [ -n "$MERGE_REF" ] && [ -n "$REMOTE" ]; then
    echo "Current tracking: $REMOTE/$MERGE_REF"
    
    # Check if remote branch exists
    if ! git ls-remote --heads $REMOTE | grep -q "$MERGE_REF"; then
        echo "⚠️  Remote branch does not exist!"
        echo "Unsetting stale tracking configuration..."
        git branch --unset-upstream
        echo "✅ Tracking configuration removed"
    else
        echo "✅ Tracking configuration is valid"
    fi
else
    echo "ℹ️  No tracking configuration set"
fi

# If on master, ensure it tracks origin/master
if [ "$CURRENT_BRANCH" = "master" ]; then
    echo "Setting master to track origin/master..."
    git branch --set-upstream-to=origin/master master
    echo "✅ Master now tracks origin/master"
fi

echo ""
echo "✅ Git configuration fixed!"
echo ""
echo "You can now run: git pull"
