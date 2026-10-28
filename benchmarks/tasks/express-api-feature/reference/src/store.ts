export interface Book {
  id: number;
  title: string;
  author: string;
  onLoanTo: string | null;
}

export interface NewBook {
  title: string;
  author: string;
}

/** The library's books, in memory. Ids start at 1 and are never reused. */
export class BookStore {
  #books = new Map<number, Book>();
  #nextId = 1;

  constructor(initial: readonly NewBook[] = []) {
    for (const book of initial) {
      this.add(book);
    }
  }

  all(): Book[] {
    return [...this.#books.values()].sort((a, b) => a.id - b.id).map((book) => ({ ...book }));
  }

  get(id: number): Book | undefined {
    const book = this.#books.get(id);
    return book ? { ...book } : undefined;
  }

  add(input: NewBook): Book {
    const book: Book = { id: this.#nextId++, title: input.title, author: input.author, onLoanTo: null };
    this.#books.set(book.id, book);
    return { ...book };
  }

  /** Lend a book: the updated book, or why not ("missing", "on-loan"). */
  lend(id: number, member: string): Book | "missing" | "on-loan" {
    const book = this.#books.get(id);
    if (!book) return "missing";
    if (book.onLoanTo !== null) return "on-loan";
    book.onLoanTo = member;
    return { ...book };
  }

  /** Take a book back: the updated book, or why not ("missing", "not-on-loan"). */
  giveBack(id: number): Book | "missing" | "not-on-loan" {
    const book = this.#books.get(id);
    if (!book) return "missing";
    if (book.onLoanTo === null) return "not-on-loan";
    book.onLoanTo = null;
    return { ...book };
  }
}
