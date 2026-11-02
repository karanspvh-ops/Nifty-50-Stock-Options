declare module 'html2pdf.js' {
  interface Html2PdfOptions {
    margin?: number | number[];
    filename?: string;
    image?: { type?: string; quality?: number };
    html2canvas?: Record<string, unknown>;
    jsPDF?: { unit?: string; format?: string | [number, number]; orientation?: string };
    pagebreak?: { mode?: string | string[] };
  }
  interface Html2PdfInstance {
    set(opts: Html2PdfOptions): Html2PdfInstance;
    from(el: HTMLElement | string): Html2PdfInstance;
    save(): Promise<void>;
    outputPdf(type?: string): Promise<Blob>;
  }
  function html2pdf(): Html2PdfInstance;
  export default html2pdf;
}
