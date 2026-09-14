// Read-only physical page accounting using the installed Node SQLite dbstat.
// No application runtime or database backend change is made by this audit.
const { DatabaseSync } = require('node:sqlite');
const fs = require('node:fs');
const path = require('node:path');
const filename = path.resolve(process.argv[2]);
if (!fs.existsSync(filename)) throw new Error('Existing SQLite file required');
const files = () => Object.fromEntries(['', '-wal', '-shm'].map(suffix =>
  [suffix || 'db', fs.existsSync(filename + suffix) ? fs.statSync(filename + suffix).size : 0]));
const before = files();
const db = new DatabaseSync(filename, { readOnly: true });
try {
  db.exec('PRAGMA query_only=ON');
  const one = (sql) => db.prepare(sql).get();
  const pageSize = one('PRAGMA page_size').page_size;
  const pageCount = one('PRAGMA page_count').page_count;
  const freeCount = one('PRAGMA freelist_count').freelist_count;
  const objects = Object.fromEntries(db.prepare('SELECT name,type,tbl_name FROM sqlite_schema').all().map(x => [x.name, x]));
  const pages = db.prepare('SELECT name,pagetype,count(*) AS pages,sum(pgsize) AS bytes,sum(payload) AS payload_bytes,sum(unused) AS unused_bytes FROM dbstat GROUP BY name,pagetype ORDER BY name,pagetype').all()
    .map(row => ({ ...row, object_type: objects[row.name]?.type || 'sqlite_internal', table_name: objects[row.name]?.tbl_name || null,
      page_structure_bytes: row.bytes - row.payload_bytes - row.unused_bytes }));
  const liveBytes = pages.reduce((n, row) => n + row.bytes, 0);
  process.stdout.write(JSON.stringify({ version: 'R16_R1_PHYSICAL_SQLITE_PAGE_AUDIT_V1', path: filename,
    node: process.version, sqlite: one('SELECT sqlite_version() AS version').version, access: 'READ_ONLY_QUERY_ONLY',
    measured_at: new Date().toISOString(), files_before: before, page_size: pageSize, page_count: pageCount,
    free_pages: freeCount, free_page_bytes: freeCount * pageSize, live_page_bytes: liveBytes,
    logical_database_bytes: pageCount * pageSize, page_accounting_unattributed_bytes: pageCount * pageSize - liveBytes - freeCount * pageSize,
    file_vs_logical_unattributed_bytes: before.db - pageCount * pageSize,
    wal_policy: 'WAL file bytes measured separately; frame payload is not falsely assigned to individual tables',
    table_index_overflow_pages: pages, files_during_read: files() }, null, 2) + '\n');
} finally { db.close(); }
