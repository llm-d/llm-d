const assert = require('node:assert/strict');
const { describe, it } = require('node:test');

const {
  listApprovedCandidatePullRequestNumbers,
  resolvePullRequestNumbers,
  evaluateAndEnqueue,
  runMergeQueueCheck,
} = require('./pr-merge-queue.cjs');

function makePr(overrides = {}) {
  return {
    id: 'PR_node_123',
    number: 42,
    state: 'OPEN',
    isDraft: false,
    headRefOid: 'deadbeef',
    mergeable: 'MERGEABLE',
    mergeStateStatus: 'CLEAN',
    reviewDecision: 'APPROVED',
    mergeQueueEntry: null,
    labels: { nodes: [] },
    ...overrides,
  };
}

describe('evaluateAndEnqueue', () => {
  it('enqueues an open non-draft approved PR with CLEAN mergeStateStatus', async () => {
    const calls = [];
    const github = {
      graphql: async (query, vars) => {
        calls.push({ query, vars });
        if (query.includes('enqueuePullRequest')) {
          return {
            enqueuePullRequest: {
              mergeQueueEntry: { id: 'MQE_1', position: 1, state: 'AWAITING_CHECKS' },
            },
          };
        }
        return { repository: { pullRequest: makePr() } };
      },
    };

    const res = await evaluateAndEnqueue({
      github,
      owner: 'llm-d',
      repo: 'llm-d',
      pull_number: 42,
      sleep: async () => {},
    });

    assert.equal(res.enqueued, true);
    assert.equal(calls.length, 2);
    assert.deepEqual(calls[1].vars, { pullRequestId: 'PR_node_123' });
  });

  for (const label of [
    'hold',
    'do-not-merge/hold',
    'do-not-merge/work-in-progress',
    'needs-rebase',
    're-approve',
  ]) {
    it(`skips PR carrying blocking label ${label}`, async () => {
      const github = {
        graphql: async () => ({
          repository: {
            pullRequest: makePr({ labels: { nodes: [{ name: label }] } }),
          },
        }),
      };

      const res = await evaluateAndEnqueue({
        github,
        owner: 'llm-d',
        repo: 'llm-d',
        pull_number: 42,
        sleep: async () => {},
      });

      assert.equal(res.enqueued, false);
      assert.equal(res.reason, 'blocking_label');
    });
  }

  it('skips PR that is not approved', async () => {
    const github = {
      graphql: async () => ({
        repository: { pullRequest: makePr({ reviewDecision: 'REVIEW_REQUIRED' }) },
      }),
    };

    const res = await evaluateAndEnqueue({
      github,
      owner: 'llm-d',
      repo: 'llm-d',
      pull_number: 42,
      sleep: async () => {},
    });

    assert.equal(res.enqueued, false);
    assert.equal(res.reason, 'not_approved');
  });

  it('skips PR already in the merge queue', async () => {
    const github = {
      graphql: async () => ({
        repository: {
          pullRequest: makePr({
            mergeQueueEntry: { id: 'MQE_1', position: 1, state: 'QUEUED' },
          }),
        },
      }),
    };

    const res = await evaluateAndEnqueue({
      github,
      owner: 'llm-d',
      repo: 'llm-d',
      pull_number: 42,
      sleep: async () => {},
    });

    assert.equal(res.enqueued, false);
    assert.equal(res.reason, 'already_queued');
  });

  it('treats a concurrent enqueue error as already queued', async () => {
    const github = {
      graphql: async (query) => {
        if (query.includes('enqueuePullRequest')) {
          throw new Error(
            'Request failed due to following response errors:\n - Pull request is already in the queue'
          );
        }
        return { repository: { pullRequest: makePr() } };
      },
    };

    const res = await evaluateAndEnqueue({
      github,
      owner: 'llm-d',
      repo: 'llm-d',
      pull_number: 42,
      sleep: async () => {},
    });

    assert.equal(res.enqueued, false);
    assert.equal(res.reason, 'already_queued');
  });

  it('retries transient BLOCKED state when approved and enqueues once CLEAN', async () => {
    let fetchCount = 0;
    const github = {
      graphql: async (query) => {
        if (query.includes('enqueuePullRequest')) {
          return {
            enqueuePullRequest: {
              mergeQueueEntry: { id: 'MQE_2', position: 2, state: 'QUEUED' },
            },
          };
        }
        fetchCount++;
        return {
          repository: {
            pullRequest: makePr({
              mergeStateStatus: fetchCount < 2 ? 'BLOCKED' : 'CLEAN',
            }),
          },
        };
      },
    };

    const res = await evaluateAndEnqueue({
      github,
      owner: 'llm-d',
      repo: 'llm-d',
      pull_number: 42,
      sleep: async () => {},
    });

    assert.equal(fetchCount, 2);
    assert.equal(res.enqueued, true);
  });

  it('does not retry BLOCKED state when retryBlocked is false', async () => {
    let fetchCount = 0;
    let sleeps = 0;
    const github = {
      graphql: async () => {
        fetchCount++;
        return {
          repository: {
            pullRequest: makePr({ mergeStateStatus: 'BLOCKED' }),
          },
        };
      },
    };

    const res = await evaluateAndEnqueue({
      github,
      owner: 'llm-d',
      repo: 'llm-d',
      pull_number: 42,
      retryBlocked: false,
      sleep: async () => {
        sleeps++;
      },
    });

    assert.equal(fetchCount, 1);
    assert.equal(sleeps, 0);
    assert.equal(res.enqueued, false);
    assert.equal(res.reason, 'checks_incomplete');
  });

  it('still retries UNKNOWN mergeable state when retryBlocked is false', async () => {
    let fetchCount = 0;
    const github = {
      graphql: async (query) => {
        if (query.includes('enqueuePullRequest')) {
          return {
            enqueuePullRequest: {
              mergeQueueEntry: { id: 'MQE_4', position: 1, state: 'QUEUED' },
            },
          };
        }
        fetchCount++;
        return {
          repository: {
            pullRequest: makePr({
              mergeable: fetchCount < 2 ? 'UNKNOWN' : 'MERGEABLE',
              mergeStateStatus: fetchCount < 2 ? 'UNKNOWN' : 'CLEAN',
            }),
          },
        };
      },
    };

    const res = await evaluateAndEnqueue({
      github,
      owner: 'llm-d',
      repo: 'llm-d',
      pull_number: 42,
      retryBlocked: false,
      sleep: async () => {},
    });

    assert.equal(fetchCount, 2);
    assert.equal(res.enqueued, true);
  });
});

describe('listApprovedCandidatePullRequestNumbers', () => {
  it('paginates, deduplicates, and filters out drafts, queued, unapproved, and blocked PRs', async () => {
    const cursors = [];
    const github = {
      graphql: async (_query, vars) => {
        cursors.push(vars.after);
        assert.match(vars.searchQuery, /repo:llm-d\/llm-d/);
        assert.match(vars.searchQuery, /review:approved/);
        if (vars.after === null) {
          return {
            search: {
              pageInfo: { hasNextPage: true, endCursor: 'cursor-1' },
              nodes: [
                { number: 10, isDraft: false, reviewDecision: 'APPROVED', mergeQueueEntry: null, labels: { nodes: [] } },
                { number: 11, isDraft: true, reviewDecision: 'APPROVED', mergeQueueEntry: null, labels: { nodes: [] } },
                { number: 12, isDraft: false, reviewDecision: 'APPROVED', mergeQueueEntry: { id: 'MQE' }, labels: { nodes: [] } },
                { number: 13, isDraft: false, reviewDecision: 'REVIEW_REQUIRED', mergeQueueEntry: null, labels: { nodes: [] } },
                { number: 14, isDraft: false, reviewDecision: 'APPROVED', mergeQueueEntry: null, labels: { nodes: [{ name: 'hold' }] } },
                { number: 15, isDraft: false, reviewDecision: 'APPROVED', mergeQueueEntry: null, labels: { nodes: [{ name: 'needs-rebase' }] } },
              ],
            },
          };
        }
        return {
          search: {
            pageInfo: { hasNextPage: false, endCursor: null },
            nodes: [
              // Duplicate across pages and a valid second candidate.
              { number: 10, isDraft: false, reviewDecision: 'APPROVED', mergeQueueEntry: null, labels: { nodes: [] } },
              { number: 20, isDraft: false, reviewDecision: 'APPROVED', mergeQueueEntry: null, labels: { nodes: [{ name: 'kind/bug' }] } },
            ],
          },
        };
      },
    };

    const nums = await listApprovedCandidatePullRequestNumbers({
      github,
      owner: 'llm-d',
      repo: 'llm-d',
    });

    assert.deepEqual(cursors, [null, 'cursor-1']);
    assert.deepEqual(nums, [10, 20]);
  });
});

describe('resolvePullRequestNumbers', () => {
  it('resolves fork PR from workflow_run via head owner:branch and head_sha', async () => {
    const github = {
      rest: {
        pulls: {
          list: async ({ head }) => {
            assert.equal(head, 'contributor:feat-branch');
            return {
              data: [
                { number: 99, head: { sha: 'other-sha' } },
                { number: 100, head: { sha: 'target-sha' } },
              ],
            };
          },
        },
      },
    };
    const context = {
      eventName: 'workflow_run',
      repo: { owner: 'llm-d', repo: 'llm-d' },
      payload: {
        workflow_run: {
          conclusion: 'success',
          pull_requests: [],
          head_repository: { owner: { login: 'contributor' } },
          head_branch: 'feat-branch',
          head_sha: 'target-sha',
        },
      },
    };

    const nums = await resolvePullRequestNumbers({ github, context });
    assert.deepEqual(nums, [100]);
  });

  it('validates collaborator permission and approval command on issue_comment', async () => {
    const github = {
      rest: {
        repos: {
          getCollaboratorPermissionLevel: async () => ({
            data: { permission: 'custom', user: { permissions: { push: true } } },
          }),
        },
      },
    };
    const context = {
      eventName: 'issue_comment',
      repo: { owner: 'llm-d', repo: 'llm-d' },
      payload: {
        issue: { number: 77, pull_request: {}, user: { login: 'author' } },
        comment: { body: '/lgtm', user: { login: 'reviewer', type: 'User' } },
      },
    };

    const nums = await resolvePullRequestNumbers({ github, context });
    assert.deepEqual(nums, [77]);
  });

  it('runs end-to-end via runMergeQueueCheck on pull_request_target', async () => {
    let enqueuedId = null;
    const github = {
      graphql: async (query, vars) => {
        if (query.includes('enqueuePullRequest')) {
          enqueuedId = vars.pullRequestId;
          return {
            enqueuePullRequest: {
              mergeQueueEntry: { id: 'MQE_3', position: 1, state: 'QUEUED' },
            },
          };
        }
        return { repository: { pullRequest: makePr({ id: 'PR_target_1', number: 55 }) } };
      },
    };
    const context = {
      eventName: 'pull_request_target',
      repo: { owner: 'llm-d', repo: 'llm-d' },
      payload: { pull_request: { number: 55 } },
    };

    await runMergeQueueCheck({ github, context, sleep: async () => {} });
    assert.equal(enqueuedId, 'PR_target_1');
  });

  it('resolves sweep candidates on schedule and empty workflow_dispatch', async () => {
    const github = {
      graphql: async () => ({
        search: {
          pageInfo: { hasNextPage: false, endCursor: null },
          nodes: [
            { number: 30, isDraft: false, reviewDecision: 'APPROVED', mergeQueueEntry: null, labels: { nodes: [] } },
          ],
        },
      }),
    };

    const scheduled = await resolvePullRequestNumbers({
      github,
      context: {
        eventName: 'schedule',
        repo: { owner: 'llm-d', repo: 'llm-d' },
        payload: {},
      },
    });
    assert.deepEqual(scheduled, [30]);

    const dispatchedSweep = await resolvePullRequestNumbers({
      github,
      context: {
        eventName: 'workflow_dispatch',
        repo: { owner: 'llm-d', repo: 'llm-d' },
        payload: { inputs: { pr_number: '' } },
      },
    });
    assert.deepEqual(dispatchedSweep, [30]);

    const dispatchedSingle = await resolvePullRequestNumbers({
      github,
      context: {
        eventName: 'workflow_dispatch',
        repo: { owner: 'llm-d', repo: 'llm-d' },
        payload: { inputs: { pr_number: '88' } },
      },
    });
    assert.deepEqual(dispatchedSingle, [88]);
  });

  it('isolates per-PR errors during a schedule sweep so remaining PRs still enqueue', async () => {
    const enqueued = [];
    let sleeps = 0;
    const github = {
      graphql: async (query, vars) => {
        if (query.includes('search(')) {
          return {
            search: {
              pageInfo: { hasNextPage: false, endCursor: null },
              nodes: [
                { number: 101, isDraft: false, reviewDecision: 'APPROVED', mergeQueueEntry: null, labels: { nodes: [] } },
                { number: 102, isDraft: false, reviewDecision: 'APPROVED', mergeQueueEntry: null, labels: { nodes: [] } },
                { number: 103, isDraft: false, reviewDecision: 'APPROVED', mergeQueueEntry: null, labels: { nodes: [] } },
              ],
            },
          };
        }
        if (query.includes('enqueuePullRequest')) {
          if (vars.pullRequestId === 'PR_101') {
            throw new Error('transient graphql error');
          }
          enqueued.push(vars.pullRequestId);
          return {
            enqueuePullRequest: {
              mergeQueueEntry: { id: 'MQE_SWEEP', position: 1, state: 'QUEUED' },
            },
          };
        }
        if (vars.number === 101) {
          return { repository: { pullRequest: makePr({ id: 'PR_101', number: 101 }) } };
        }
        if (vars.number === 102) {
          return {
            repository: {
              pullRequest: makePr({ id: 'PR_102', number: 102, mergeStateStatus: 'BLOCKED' }),
            },
          };
        }
        return { repository: { pullRequest: makePr({ id: 'PR_103', number: 103 }) } };
      },
    };

    await assert.rejects(
      () =>
        runMergeQueueCheck({
          github,
          context: {
            eventName: 'schedule',
            repo: { owner: 'llm-d', repo: 'llm-d' },
            payload: {},
          },
          sleep: async () => {
            sleeps++;
          },
        }),
      /Failed to process 1 pull request\(s\): #101/
    );

    assert.deepEqual(enqueued, ['PR_103']);
    assert.equal(sleeps, 0);
  });
});
