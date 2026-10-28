export interface Book {
  id: number;
  title: string;
  author: string;
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
    const book: Book = { id: this.#nextId++, title: input.title, author: input.author };
    this.#books.set(book.id, book);
    return { ...book };
  }
}
