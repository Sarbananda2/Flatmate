/**
 * Commit message rules, checked by the commit-msg hook in .husky/.
 *
 * Messages follow Conventional Commits:
 *
 *   <type>(<optional scope>): <subject in lower case, no full stop>
 *
 *   <optional body, lines up to 100 characters>
 *
 * Types: feat, fix, docs, style, refactor, perf, test, build, ci, chore, revert.
 */
export default {
  extends: ['@commitlint/config-conventional'],
}
