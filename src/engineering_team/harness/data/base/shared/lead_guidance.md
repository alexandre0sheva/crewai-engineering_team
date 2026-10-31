- Do the work yourself by default; a teammate costs a session of its own and starts cold.
- Independent parts with disjoint paths (two modules, a backend and a frontend) can go to teammates
  side by side with Delegate Parallel while you integrate.
- A different stack from the one you are working in (a frontend while you do the backend) is worth
  a Delegate to the teammate for that stack.
- Once a non-trivial change is ready, Ask Reviewer once and fix what matters.
- A check that still fails after two tries of your own goes to Ask Debugger.
- A change touching authentication, secrets, crypto, SQL, subprocesses or deserialisation gets Ask
  Security Review after your last change.
- Large or unclear work gets Ask Architect (a plan draft) or Ask Analyst (the criteria) before you
  build.
- Brief a teammate like a colleague who has not seen your session - the goal, the files, the
  criteria, what done looks like - and give it only the paths it needs.
