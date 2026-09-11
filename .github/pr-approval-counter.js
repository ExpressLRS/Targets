module.exports = async ({ github, context }) => {
  const owner = context.repo.owner;
  const repo = context.repo.repo;

  // Helper function to process a single PR's approval labels
  async function processPullRequest(prNumber, targetBranch) {
    // 1. Get the minimum required approvals from branch protection rules
    let requiredApprovals = 1;
    try {
      const { data: protection } = await github.rest.repos.getBranchProtection({
        owner,
        repo,
        branch: targetBranch,
      });
      if (protection.required_pull_request_reviews) {
        requiredApprovals = protection.required_pull_request_reviews.required_approving_review_count || 1;
      }
    } catch (e) {
      console.log(`No branch protection rule found for ${targetBranch}. Defaulting to 1 required approval.`);
    }

    // 2. Get all reviews for the current PR
    const { data: reviews } = await github.rest.pulls.listReviews({
      owner,
      repo,
      pull_number: prNumber,
    });

    // 3. Track the latest state of each unique reviewer
    const reviewerStates = {};
    for (const review of reviews) {
      reviewerStates[review.user.login] = review.state;
    }

    // 4. Count unique active approvals
    const approvalCount = Object.values(reviewerStates).filter(state => state === 'APPROVED').length;

    // 5. Get the existing labels on the PR
    const { data: prDetails } = await github.rest.pulls.get({
      owner,
      repo,
      pull_number: prNumber,
    });
    const currentLabels = prDetails.labels.map(l => l.name);

    // 6. Handle the zero-approvals case
    if (approvalCount === 0) {
      for (const label of currentLabels) {
        if (label.startsWith('Approved: ')) {
          await github.rest.issues.removeLabel({
            owner,
            repo,
            issue_number: prNumber,
            name: label,
          }).catch(() => {});
        }
      }
      return;
    }

    // 7. Define label name and color if approvalCount > 0
    const targetLabel = `Approved: ${approvalCount}/${requiredApprovals}`;
    let labelColor = 'FEF2C0'; // Yellow (In Progress)
    if (approvalCount >= requiredApprovals) {
      labelColor = '0E8A16'; // Vibrant Green (Target Met)
    }

    // 8. Ensure the label exists in the repo with the correct color
    try {
      await github.rest.issues.createLabel({
        owner,
        repo,
        name: targetLabel,
        color: labelColor,
        description: `PR has ${approvalCount} of ${requiredApprovals} required approvals.`
      });
    } catch (error) {
      await github.rest.issues.updateLabel({
        owner,
        repo,
        name: targetLabel,
        color: labelColor
      }).catch(() => {});
    }

    // 9. Clean up obsolete counter labels
    for (const label of currentLabels) {
      if (label.startsWith('Approved: ') && label !== targetLabel) {
        await github.rest.issues.removeLabel({
          owner,
          repo,
          issue_number: prNumber,
          name: label,
        }).catch(() => {});
      }
    }

    // 10. Apply the new dynamic label
    if (!currentLabels.includes(targetLabel)) {
      await github.rest.issues.addLabels({
        owner,
        repo,
        issue_number: prNumber,
        labels: [targetLabel],
      });
    }
  }

  // --- Execution Flow ---
  if (context.eventName === 'workflow_dispatch') {
    const { data: openPRs } = await github.rest.pulls.list({
      owner,
      repo,
      state: 'open',
      per_page: 100
    });

    console.log(`Manually triggered. Bulk updating ${openPRs.length} open pull requests...`);
    for (const pr of openPRs) {
      await processPullRequest(pr.number, pr.base.ref);
    }
  } else {
    // Normal single-PR event (review submitted, push, etc.)
    const prNumber = context.payload.pull_request.number;
    const targetBranch = context.payload.pull_request.base.ref;
    await processPullRequest(prNumber, targetBranch);
  }
};
