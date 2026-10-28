/** A duration or command line the user got wrong: printed as `error: ...`, exit status 2. */
export class DurationError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "DurationError";
  }
}
