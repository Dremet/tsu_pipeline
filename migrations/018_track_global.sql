-- 018: distinguish "not assigned yet" ('xx', globe placeholder) from
-- "deliberately global/fictional" ('un'). The 017 seed marked researched
-- fictional tracks as 'xx'; flip those to 'un' so the admin page only
-- flags genuinely unassigned tracks.
BEGIN;

UPDATE webadmin.track_countries SET country_code = 'un', updated_at = now()
 WHERE country_code = 'xx' AND track_name IN (
    'Ayertown GP',
    'Bangowitch Circuit 1.02 (F3O)',
    'Beerlowtown halloween v1.02',
    'Bombtrack',
    'Broadbean Raceway V1.0',
    'Buffalo Hill - Rallycross v1.0',
    'CSup - Lost Lagoons v1',
    'Candyville Sfinx v1.3',
    'City heights',
    'Elethasia Island Circuit v1.01',
    'Firston',
    'Grizzlemaw Glacier 1.1',
    'Hassain Sula GP v1.00',
    'Kartland 2.0',
    'Magic Mushroom Forest',
    'Mountains of Madness',
    'Nanoli Full Circuit v1.4',
    'Off The Road',
    'Owl Forest Hills',
    'Parasite Coast Run v1.00',
    'Quarry',
    'RC Playground Zero v3',
    'Rosenholm Circuit',
    'Rushgrove',
    'Sliders Island v1.0',
    'Thunder Point',
    'Tilksport GP v2',
    'Trial Mountain Circuit v2.02',
    'TurboCross(TM) v1.0',
    'VSR-Homeland V1.5',
    'Xerkox v6'
);

COMMIT;
