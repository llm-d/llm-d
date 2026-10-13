// Shared helpers and job handlers for .github/workflows/pr-needs-rebase.yaml.

const NEEDS_REBASE_LABEL = 'needs-rebase';
const RE_APPROVE_LABEL = 're-approve';
const LGTM_LABEL = 'lgtm';
const APPROVED_LABEL = 'approved';

// Line-anchored Prow approval commands; excludes `/lgtm cancel` and `/approve cancel`.
const APPROVAL_CMD_RE = /^\s*\/(lgtm|approve)\s*$/i;
const CANCEL_APPROVAL_CMD_RE = /^\s*(?:\/(lgtm|approve)\s+cancel|\/remove-(lgtm|approve))\s*$/i;

const defaultSleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

function isHumanReviewer(user, prAuthor) {
  return Boolean(
    user &&
      user.login &&
      user.login !== prAuthor &&
      user.type !== 'Bot' &&
      !user.login.endsWith('[bot]')
  );
}

function hasApprovalCommand(body) {
  let approved = false;
  for (const line of String(body || '').split(/\r?\n/)) {
    if (CANCEL_APPROVAL_CMD_RE.test(line)) {
      approved = false;
    } else if (APPROVAL_CMD_RE.test(line)) {
      approved = true;
    }
  }
  return approved;
}

async function ensureLabel(github, owner, repo, name, color, description) {
  try {
    await github.rest.issues.getLabel({ owner, repo, name });
  } catch (e) {
    if (e.status === 404) {
      await github.rest.issues.createLabel({ owner, repo, name, color, description });
    } else {
      throw e;
    }
  }
}

async function removeLabelIfPresent(github, owner, repo, issue_number, name) {
  try {
    await github.rest.issues.removeLabel({
      owner,
      repo,
      issue_number,
      name,
    });
    return true;
  } catch (e) {
    if (e.status !== 404) throw e;
    return false;
  }
}

function buildNeedsRebaseComment(pull_number, author, baseRef) {
  return [
    `@${author} This PR has merge conflicts with \`${baseRef}\`. Please rebase on \`${baseRef}\` and push to resolve them:`,
    '```bash',
    `gh pr checkout ${pull_number}`,
    `git fetch origin ${baseRef}`,
    `git rebase origin/${baseRef}`,
    '# resolve conflicts',
    'git push --force-with-lease',
    '```',
  ].join('\n');
}

async function applyNeedsRebase({
  github,
  owner,
  repo,
  pull_number,
  author,
  baseRef,
  hasNeedsRebase,
  hasReApprove,
}) {
  if (hasReApprove) {
    await removeLabelIfPresent(github, owner, repo, pull_number, RE_APPROVE_LABEL);
  }
  if (!hasNeedsRebase) {
    await ensureLabel(
      github,
      owner,
      repo,
      NEEDS_REBASE_LABEL,
      'ff578a',
      'PR has merge conflicts and needs a rebase'
    );
    // Add the label before commenting so a cancelled or retried run cannot post duplicate comments.
    await github.rest.issues.addLabels({
      owner,
      repo,
      issue_number: pull_number,
      labels: [NEEDS_REBASE_LABEL],
    });
    await github.rest.issues.createComment({
      owner,
      repo,
      issue_number: pull_number,
      body: buildNeedsRebaseComment(pull_number, author, baseRef),
    });
  }
}

async function collectApprovalState({ github, owner, repo, pull_number, prAuthor, headSha }) {
  const [reviews, events] = await Promise.all([
    github.paginate(github.rest.pulls.listReviews, {
      owner,
      repo,
      pull_number,
      per_page: 100,
    }),
    github.paginate(github.rest.issues.listEvents, {
      owner,
      repo,
      issue_number: pull_number,
      per_page: 100,
    }),
  ]);

  // Only treat a DISMISSED review as a prior approval if its review_dismissed event
  // records that the review's previous state was `approved` (not `changes_requested`).
  const dismissedApprovalReviewIds = new Set(
    events
      .filter(
        (ev) =>
          ev.event === 'review_dismissed' &&
          ev.dismissed_review &&
          String(ev.dismissed_review.state).toLowerCase() === 'approved'
      )
      .map((ev) => ev.dismissed_review.review_id)
  );

  const latestByUser = new Map();
  let approvedOnHead = false;
  for (const r of reviews) {
    if (!isHumanReviewer(r.user, prAuthor)) continue;
    const user = r.user.login;
    if (r.state === 'APPROVED') {
      latestByUser.set(user, 'APPROVED');
      if (r.commit_id === headSha) {
        approvedOnHead = true;
      }
    } else if (r.state === 'CHANGES_REQUESTED') {
      latestByUser.set(user, 'CHANGES_REQUESTED');
    } else if (r.state === 'DISMISSED') {
      if (dismissedApprovalReviewIds.has(r.id)) {
        latestByUser.set(user, 'DISMISSED_APPROVAL');
      } else {
        latestByUser.delete(user);
      }
    }
  }

  const priorApprovers = new Set();
  let hasChangesRequested = false;
  for (const [user, state] of latestByUser.entries()) {
    if (state === 'CHANGES_REQUESTED') {
      hasChangesRequested = true;
    } else if (state === 'APPROVED' || state === 'DISMISSED_APPROVAL') {
      priorApprovers.add(user);
    }
  }

  // Track Prow label/comment approvals chronologically so canceled approvals are excluded.
  let sawApprovalLabelEvent = false;
  const labelApprovers = new Set();
  for (const ev of events) {
    if (!ev.label || (ev.label.name !== LGTM_LABEL && ev.label.name !== APPROVED_LABEL)) {
      continue;
    }
    sawApprovalLabelEvent = true;
    if (ev.event === 'labeled' && isHumanReviewer(ev.actor, prAuthor)) {
      labelApprovers.add(ev.actor.login);
    } else if (ev.event === 'unlabeled' && isHumanReviewer(ev.actor, prAuthor)) {
      labelApprovers.delete(ev.actor.login);
    }
  }

  const commentApprovers = new Set();
  if (sawApprovalLabelEvent) {
    const comments = await github.paginate(github.rest.issues.listComments, {
      owner,
      repo,
      issue_number: pull_number,
      per_page: 100,
    });
    for (const c of comments) {
      if (!isHumanReviewer(c.user, prAuthor)) continue;
      for (const line of String(c.body || '').split(/\r?\n/)) {
        if (CANCEL_APPROVAL_CMD_RE.test(line)) {
          commentApprovers.delete(c.user.login);
          labelApprovers.delete(c.user.login);
        } else if (APPROVAL_CMD_RE.test(line)) {
          commentApprovers.add(c.user.login);
        }
      }
    }
  }

  for (const user of [...labelApprovers, ...commentApprovers]) {
    if (latestByUser.get(user) !== 'CHANGES_REQUESTED') {
      priorApprovers.add(user);
    }
  }

  return {
    priorApprovers,
    wasPreviouslyApproved: priorApprovers.size > 0,
    hasChangesRequested,
    approvedOnHead,
  };
}

async function clearReApprove({ github, context }) {
  const owner = context.repo.owner;
  const repo = context.repo.repo;

  if (context.eventName === 'issue_comment') {
    const issue = context.payload.issue;
    const comment = context.payload.comment;
    if (!issue || !issue.pull_request || !comment) return;
    if (!hasApprovalCommand(comment.body)) {
      return;
    }
    const prAuthor = issue.user?.login;
    if (!isHumanReviewer(comment.user, prAuthor)) {
      console.log(`Ignoring self or bot approval comment by @${comment.user?.login}.`);
      return;
    }
    const perm = await github.rest.repos.getCollaboratorPermissionLevel({
      owner,
      repo,
      username: comment.user.login,
    });
    if (!['admin', 'write', 'maintain'].includes(perm.data.permission)) {
      console.log(
        `User @${comment.user.login} does not have write permission (${perm.data.permission}); skipping.`
      );
      return;
    }
    const removed = await removeLabelIfPresent(
      github,
      owner,
      repo,
      issue.number,
      RE_APPROVE_LABEL
    );
    if (removed) {
      console.log(`Removed ${RE_APPROVE_LABEL} label from PR #${issue.number}.`);
    }
    return;
  }

  const pull_number = context.payload.pull_request?.number;
  if (!pull_number) return;
  const removed = await removeLabelIfPresent(
    github,
    owner,
    repo,
    pull_number,
    RE_APPROVE_LABEL
  );
  if (removed) {
    console.log(`Removed ${RE_APPROVE_LABEL} label from PR #${pull_number}.`);
  }
}

async function checkSinglePr({ github, context, sleep = defaultSleep }) {
  const owner = context.repo.owner;
  const repo = context.repo.repo;
  const pull_number = context.payload.pull_request.number;

  let pr = null;
  for (let attempt = 0; attempt < 5; attempt++) {
    const resp = await github.rest.pulls.get({ owner, repo, pull_number });
    pr = resp.data;
    if (pr.mergeable !== null) break;
    await sleep(3000 * (attempt + 1));
  }

  const prAuthor = pr.user.login;
  const labels = (pr.labels || []).map((l) => l.name);
  const hasNeedsRebase = labels.includes(NEEDS_REBASE_LABEL);
  const hasReApprove = labels.includes(RE_APPROVE_LABEL);

  if (pr.mergeable === true) {
    if (!hasNeedsRebase) {
      return;
    }

    console.log(`PR #${pull_number} is now mergeable; removing ${NEEDS_REBASE_LABEL}.`);
    await removeLabelIfPresent(github, owner, repo, pull_number, NEEDS_REBASE_LABEL);

    // On synchronize, any existing `lgtm` label predates this push (prow-pr-remove-lgtm
    // runs concurrently on `pull_request` and lacks write access on fork PRs).
    if (context.payload.action === 'synchronize' && labels.includes(LGTM_LABEL)) {
      await removeLabelIfPresent(github, owner, repo, pull_number, LGTM_LABEL);
    }

    const { priorApprovers, wasPreviouslyApproved, hasChangesRequested, approvedOnHead } =
      await collectApprovalState({
        github,
        owner,
        repo,
        pull_number,
        prAuthor,
        headSha: pr.head.sha,
      });

    if (wasPreviouslyApproved && !hasChangesRequested && !approvedOnHead && !hasReApprove) {
      await ensureLabel(
        github,
        owner,
        repo,
        RE_APPROVE_LABEL,
        '0e8a16',
        'PR was rebased after prior approval and needs re-approval'
      );
      await github.rest.issues.addLabels({
        owner,
        repo,
        issue_number: pull_number,
        labels: [RE_APPROVE_LABEL],
      });
      const mentions = Array.from(priorApprovers)
        .map((u) => `@${u}`)
        .join(' ');
      const commentPrefix = mentions ? `${mentions} ` : '';
      const body = `${commentPrefix}@${prAuthor} has rebased this PR and it is now conflict-free. Please take another look and re-approve when ready.`;
      await github.rest.issues.createComment({
        owner,
        repo,
        issue_number: pull_number,
        body,
      });
      console.log(
        `Added ${RE_APPROVE_LABEL} label and pinged (${mentions || 'maintainers'}) on PR #${pull_number}.`
      );
    }
  } else if (pr.mergeable === false) {
    await applyNeedsRebase({
      github,
      owner,
      repo,
      pull_number,
      author: prAuthor,
      baseRef: pr.base.ref,
      hasNeedsRebase,
      hasReApprove,
    });
  }
}

async function fetchPrsInBatches(github, owner, repo, numbers, batchSize = 10) {
  const results = new Map();
  for (let i = 0; i < numbers.length; i += batchSize) {
    const batch = numbers.slice(i, i + batchSize);
    const fetched = await Promise.all(
      batch.map(async (pull_number) => {
        const { data } = await github.rest.pulls.get({ owner, repo, pull_number });
        return data;
      })
    );
    for (const pr of fetched) {
      results.set(pr.number, pr);
    }
  }
  return results;
}

async function checkOpenPrs({ github, context, sleep = defaultSleep }) {
  const owner = context.repo.owner;
  const repo = context.repo.repo;
  const baseBranch =
    context.eventName === 'push' && context.ref
      ? context.ref.replace(/^refs\/heads\//, '')
      : context.payload.repository?.default_branch || 'main';

  await ensureLabel(
    github,
    owner,
    repo,
    NEEDS_REBASE_LABEL,
    'ff578a',
    'PR has merge conflicts and needs a rebase'
  );

  // Give GitHub a brief window to invalidate mergeability after a push.
  await sleep(5000);

  const prs = await github.paginate(github.rest.pulls.list, {
    owner,
    repo,
    state: 'open',
    base: baseBranch,
    per_page: 100,
  });

  const openPrs = prs.filter((item) => !item.draft);
  if (openPrs.length === 0) return;

  // Initial pass across all open non-draft PRs triggers GitHub's background test-merge
  // calculation concurrently, followed by bounded retry rounds for remaining null entries.
  const prMap = await fetchPrsInBatches(
    github,
    owner,
    repo,
    openPrs.map((p) => p.number)
  );

  for (let round = 0; round < 3; round++) {
    const pendingNumbers = Array.from(prMap.values())
      .filter((p) => p.mergeable === null)
      .map((p) => p.number);
    if (pendingNumbers.length === 0) break;
    await sleep(4000);
    const refreshed = await fetchPrsInBatches(github, owner, repo, pendingNumbers);
    for (const [num, pr] of refreshed.entries()) {
      prMap.set(num, pr);
    }
  }

  for (const item of openPrs) {
    const pr = prMap.get(item.number) || item;
    const pull_number = pr.number;
    const author = pr.user.login;
    const labels = (pr.labels || []).map((l) => l.name);
    const hasNeedsRebase = labels.includes(NEEDS_REBASE_LABEL);
    const hasReApprove = labels.includes(RE_APPROVE_LABEL);

    console.log(
      `PR #${pull_number} (@${author}): mergeable=${pr.mergeable}, hasNeedsRebase=${hasNeedsRebase}`
    );

    if (pr.mergeable === false) {
      await applyNeedsRebase({
        github,
        owner,
        repo,
        pull_number,
        author,
        baseRef: pr.base?.ref || baseBranch,
        hasNeedsRebase,
        hasReApprove,
      });
    } else if (pr.mergeable === true && hasNeedsRebase) {
      console.log(`Removing ${NEEDS_REBASE_LABEL} from PR #${pull_number}`);
      await removeLabelIfPresent(github, owner, repo, pull_number, NEEDS_REBASE_LABEL);
    }
  }
}

module.exports = {
  NEEDS_REBASE_LABEL,
  RE_APPROVE_LABEL,
  APPROVAL_CMD_RE,
  CANCEL_APPROVAL_CMD_RE,
  isHumanReviewer,
  hasApprovalCommand,
  ensureLabel,
  removeLabelIfPresent,
  buildNeedsRebaseComment,
  applyNeedsRebase,
  collectApprovalState,
  clearReApprove,
  checkSinglePr,
  checkOpenPrs,
};
