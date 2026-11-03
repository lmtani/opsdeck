// Line icons shared by the sidebars, drawn like the eye of watched filters:
// 24-unit box, 2-unit stroke in the text color, sized by the caller.

const svg = (size, body) =>
  `<svg viewBox="0 0 24 24" width="${size}" height="${size}" aria-hidden="true" fill="none" ` +
  `stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">${body}</svg>`;

export const eyeIcon = (size = 13) =>
  svg(
    size,
    '<path d="M2 12s3.6-7 10-7 10 7 10 7-3.6 7-10 7S2 12 2 12Z"/>' +
      '<circle cx="12" cy="12" r="3" fill="currentColor" stroke="none"/>'
  );

export const bellIcon = (size = 13) =>
  svg(
    size,
    '<path d="M6 8a6 6 0 0 1 12 0c0 7 3 9 3 9H3s3-2 3-9"/><path d="M10.3 21a1.94 1.94 0 0 0 3.4 0"/>'
  );
