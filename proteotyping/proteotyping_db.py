#!/usr/bin/env python3.5
# Fredrik Boulund
# (c) 2015-11-15

import sys
from sys import argv, exit
from collections import OrderedDict
from functools import lru_cache
import requests
import sqlite3
import itertools
import time
import re
import logging
import argparse
import fnmatch
import os
import gzip

from ete3 import NCBITaxa

try: 
    from proteotyping.utils import read_fasta, find_files, grouper, existing_file
except ImportError:
    from utils import read_fasta, find_files, grouper, existing_file


def gi_taxid_generator(gi_taxid_dmp):
    """
    Generate (gi, taxid) tuples from gi_taxid_dmp.
    """
    with open(gi_taxid_dmp) as f:
        for line in f:
            gi, taxid = line.split()
            yield int(gi), int(taxid)


def efetch_taxid(accno):
    payload = {"db": "nuccore", 
               "id": accno,
               "rettype": "fasta",
               "retmode": "xml"}
    logging.debug("Requesting taxid for accno %s via Entrez E-utils", accno)
    xml = requests.get("http://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi", params=payload)
    taxid = int(xml.text.split("taxid>")[1].split("<")[0])
    if taxid:
        return taxid
    else:
        return None


def create_header_taxid_mappings(refdir, pattern, gi_taxid):
    """
    Generates a list of header:taxid pairs using gi numbers in FASTA headers.
    
    Reads gi numbers from FASTA headers (e.g. gi|52525|). The sequence
    header is split on the first space character. 
    """
    logging.getLogger("requests").setLevel(logging.WARNING)
    for fasta_file in find_files(refdir, pattern):
        for seqinfo in read_fasta(fasta_file):
            header = seqinfo[0].split()[0]
            gi = int(header.split("gi|")[1].split("|")[0])
            taxid = gi_taxid[gi]
            if taxid:
                yield header, taxid
            else:
                logging.debug("Found no taxid mapping for gi: %s in the taxdump db", header)
                accno = header.split("ref|")[1].split("|")[0]
                taxid = efetch_taxid(accno)
                if taxid:
                    yield header, taxid
                else:
                    logging.debug("Found in taxid for accno: %s", accno)


class Taxdump_DB_wrapper():
    def __init__(self, sqlite3db_file, gi_taxid_dmp, rows_per_chunk=200000):
        if sqlite3db_file is None:
            self.sqlite3db_file = "gi_taxid.db"
        else:
            self.sqlite3db_file = sqlite3db_file
        if os.path.isfile(self.sqlite3db_file):
            logging.debug("Found previous DB file %s", self.sqlite3db_file)
            self.con = sqlite3.connect(self.sqlite3db_file)
        else:
            logging.debug("Found no previous DB file, creating new: %s", self.sqlite3db_file)
            if not gi_taxid_dmp: 
                raise Exception("Argument gi_taxid_dmp required to create new DB.")
            self.con = self.create_gi_taxid_db(self.sqlite3db_file, gi_taxid_dmp, rows_per_chunk)
    
    def create_gi_taxid_db(self, sqlite3db_file, gi_taxid_dmp, rows_per_chunk):
        """
        Create a (huge) sqlite3 DB with gi:taxid mappings.
        Consumes about 13 GiB storage and takes >1500 secs to make.

        Got some wild ideas on performance optimization from:
        http://stackoverflow.com/questions/1711631/improve-insert-per-second-performance-of-sqlite
        """

        logging.info("Creating header:taxid mappings...")

        con = sqlite3.connect(sqlite3db_file)
        con.execute("PRAGMA journal_mode = MEMORY")
        con.execute("PRAGMA synchronous = OFF")
        con.execute("CREATE TABLE gi_taxid(gi INT PRIMARY KEY, taxid INT)")
        tic = time.time()
        for pairs in grouper(rows_per_chunk, gi_taxid_generator(gi_taxid_dmp)):
            con.executemany("INSERT INTO gi_taxid VALUES (?, ?)", pairs)
        con.commit()
        toc = time.time()-tic
        num_mappings = con.execute("SELECT Count(*) FROM gi_taxid").fetchone()[0]
        logging.debug("Inserted %i mappings in %2.2f seconds.", num_mappings, toc)
        return con

    def __getitem__(self, key):
        taxid = self.con.execute("SELECT taxid FROM gi_taxid WHERE gi = ?", (key,)).fetchone()
        try:
            return taxid[0]
        except TypeError:
            return 

    def __len__(self):
        num_mappings = self.con.execute("SELECT Count(*) FROM gi_taxid").fetchone()[0]
        return num_mappings


def create_header_taxid_file(sqlite3db, refdir, gi_taxid_dmp, outfile):
    """
    Create a two column text file containing FASTA header->taxids mappings.

    :param sqlite3db:  path to sqlite3 database storing gi->taxid mappings.
    :param refdir:  path to NCBI RefSeq base directory.
    :param gi_taxid_dmp:  path to file 'gi_taxid_nucl.dmp' from NCBI 
            taxonomy dump.
    :param outfile:  file to write header->taxid mappings to.
    :return:  None.
    """

    tic = time.time()
    gi_taxid_db = Taxdump_DB_wrapper(sqlite3db, gi_taxid_dmp)
    logging.debug("Database with %i entries created/loaded in %s seconds.", len(gi_taxid_db), time.time()-tic)

    tic = time.time()
    logging.debug("Parsing FASTA files under %s ...", options.refdir)
    logging.debug("Writing results to %s...", options.outfile)
    header_taxids = create_header_taxid_mappings(options.refdir, options.globpattern_fasta, gi_taxid_db)
    with open(options.outfile, "w") as outfile:
        for count, header_taxid in enumerate(header_taxids, start=1):
            outfile.write("{}\t{}\n".format(*header_taxid))
    logging.debug("Parsed and wrote %i header:taxid mappings in %s seconds.", count, time.time()-tic)


class NCBITaxa_mod(NCBITaxa):
    """
    Extended/improved version of ete3.NCBITaxa.

    Improved functionality:
    - Added ability to select a custom dbfile location, instead of the default.
    - Made default dbfile location in user home dir cross-platform. 
    """

    def __init__(self, dbfile=False):
        if not dbfile:
            homedir = os.path.expanduser("~")
            self.dbfile = os.path.join(homedir, ".etetoolkit", "taxa.sqlite")
        elif dbfile and not os.path.exists(dbfile):
            logging.info("Downloading NCBI Taxonomy database")
            self.dbfile = dbfile
            self.update_taxonomy_database()
        else:
            self.dbfile = dbfile

        self.db = None
        self._connect()
 
    def expand_taxonomy_db(self, refseq_ver, taxonomy_ver, comment):
        """
        Prepare taxonomy DB for use with proteotyping.

        Expands the ETE3-based taxonomy database with additional tables for
        storing discriminative peptide information and genome annotations.
        Will remove tables "version", "peptides", "discriminative", "refseqs",
        and "annotations" from database if they exist.

        :param refseq_ver:  String specifying what version of RefSeq was
                when creating the db.
        :param taxonomy_ver:  String specifying what version of NCBI
                Taxonomoy was used when creating the db.
        :param comment:  String containing a comment describing the db.
        :return:  None.
        """
        
        creation_date = time.strftime("%Y-%M-%d")
        self.db.execute("DROP TABLE IF EXISTS version")
        self.db.execute("DROP TABLE IF EXISTS peptides")
        self.db.execute("DROP TABLE IF EXISTS discriminative")
        self.db.execute("DROP TABLE IF EXISTS refseqs")
        self.db.execute("DROP TABLE IF EXISTS annotations")
        self.db.execute("CREATE TABLE version(created TEXT, refseq TEXT, taxonomy TEXT, comment TEXT)")
        self.db.execute("INSERT INTO version VALUES (?, ?, ?, ?)", (creation_date, refseq_ver, taxonomy_ver, comment))
        self.db.execute("CREATE TABLE peptides(peptide TEXT, target TEXT, start INT, end INT, identity INT, matches INT)")
        self.db.execute("CREATE TABLE discriminative(peptide TEXT PRIMARY KEY REFERENCES peptides(peptide), taxid INT REFERENCES species(taxid))")
        self.db.execute("CREATE TABLE refseqs(header TEXT PRIMARY KEY, taxid INT)")
        self.db.execute("CREATE TABLE annotations(header TEXT REFERENCES refseqs(header), start INT, end INT, annotation TEXT)")
        self.db.execute("CREATE TABLE gene(taxid TEXT REFERENCES species(taxid), gene_id INT PRIMARY KEY, symbol TEXT, description TEXT)")

    def insert_refseqs_into_db(self, refseqs):
        """
        Insert reference sequences into DB.

        :param refseqs:  Nested list/tuple with sequence header taxid pairs.
        """

        self.db.executemany("INSERT INTO refseqs VALUES (?, ?)", refseqs)
        self.db.commit()
    
    def insert_gene_info(self, gene_infos):
        """
        Insert gene info from NCBI Gene into DB.

        :param gene_infos:  List of tuples with gene info (tax_id, GeneID,
                Symbol, description).
        :return:  None.
        """
        self.db.executemany("INSERT INTO gene VALUES (?, ?, ?, ?)", gene_infos)
        self.db.commit()

    @lru_cache(maxsize=2)
    def find_refseq_header(self, sequence_identifier):
        """
        Find a reference sequence header from the DB using a substring.
        """
        header = self.db.execute("SELECT header FROM refseqs WHERE header LIKE ?", ("%"+sequence_identifier+"%",)).fetchone()[0]
        return header

    def insert_annotations(self, annotations):
        """
        Insert sequence annotations from GFF files.

        :param annotations:  list of tuples with annotation information
                (sequence, start, end, annotation).
        :return:  None.
        """
        self.db.executemany("INSERT INTO annotations VALUES (?, ?, ?, ?)", annotations)
        self.db.commit()

    def extend_taxonomy_db(self, species_info_tuples):
        """
        Insert additional taxonomic nodes to taxonomy

        :param dbfile: path to dbfile.
        :param species_info_tuples: List/tuple of tuples containing 
                (taxid, parent, spname, common, rank, track), e.g.
                (12908, 1, "Viruses", "", "superkingdom", "10239,1").
        :return: None
        """

        print("NOT YET IMPLEMENTED") # TODO: Implement taxonomy db extension
        #con.executemany("INSERT INTO species VALUES (?, ?, ?, ?, ?, ?)", (taxid, parent, spname, common, rank, track))

    def dump_db(self, outputfile):
        """
        Dump entire DB in SQL text format.
        """

        if not outputfile.endswith(".gz"):
            outputfilename = outputfile+".gz"
        else:
            outputfilename = outputfile
        with gzip.GzipFile(outputfilename, "w") as out:
            logging.debug("Writing gzipped SQL DB dump to %s...", outputfilename)
            for line in self.db.iterdump():
                out.write(bytes("%s\n" % line, "utf-8"))
            logging.debug("Finished writing SQL DB dump.")


def parse_gff(filename):
    """
    Parse gene annotations from GFF file.

    Parses GFF version 3 with the following columns:
    1   sequence
    2   source
    3   feature
    4   start
    5   end
    6   score
    7   strand
    8   phase
    9   attributes

    :param filename:  path to gff file.
    :return:  (sequence, start, end, attributes)
    """
    with open(filename) as f:
        logging.debug("Parsing %s...", filename)
        line = f.readline()
        if not line.startswith("##gff-version 3"):
            raise Exception("Parse error, wrong gff version or not gff file.")
        while line.startswith("#"):
            line = f.readline()
        while line:
            try:
                sequence, source, feature, start, end, score, strand, phase, attributes = line.split("\t")
                yield (sequence, start, end, attributes)
            except ValueError:
                if line == "###":
                    logging.debug("Reached end of %s", filename)
                else:
                    logging.error("Couldn't parse gff, the offending line was:\n%s", line)
            line = f.readline()


def parse_annotations(proteodb, annotations_dir, pattern):
    """
    Recursively find gff files in refdir.

    """
    for gff_file in find_files(annotations_dir, pattern):
        for annotation_info in parse_gff(gff_file):
            header = proteodb.find_refseq_header(annotation_info[0])
            yield (header, *annotation_info[1:])


def parse_refseqs(filename):
    """
    Parse refseq:taxid mappings from file.
    """

    with open(filename) as f:
        for line in f:
            header, taxid = line.split()
            yield header, int(taxid)


def parse_gene_info(filename):
    """
    Parse tax_id, GeneID, Symbol, description from NCBI Gene (gene_info).
    
    :param filename:  path to NCBI gene_info file.
    """
    with open(filename) as f:
        f.readline() # Skip the header line
        for line in f:
            taxid, geneid, symbol, _, _, _, _, _, description, *_ = line.split("\t")
            yield (int(taxid), geneid, symbol, description)


def prepare_db(dbfile, refseqs, gene_info, annotations_dir, globpattern_gff, taxonomy_ver, refseq_ver, comment):
    """
    Prepare DB based on ETE3 NCBITaxa.
    """
    
    n = NCBITaxa_mod(dbfile)
    n.expand_taxonomy_db("2015-11-17", "2015-11-17", "second try")
    n.insert_refseqs_into_db(parse_refseqs(refseqs))
    #n.insert_gene_info(parse_gene_info(gene_info))
    #n.insert_annotations(parse_annotations(n, annotations_dir, globpattern_gff))
    #n.dump_db("taxonomy.db.gz")


def parse_commandline(argv):
    """
    Parse commandline arguments.
    """

    desc = """Prepare a proteotyping database. Fredrik Boulund (c) 2015."""
    parser = argparse.ArgumentParser(description=desc)
    subparsers = parser.add_subparsers(dest="subcommand", help="Choose a sub-command.")

    parser_refseqs = subparsers.add_parser("header_mappings", 
            help="Prepare a list of 'sequence header->taxid' mappings for reference sequences.")
    parser_proteodb = subparsers.add_parser("proteodb",
            help="Prepare a proteotyping database based on NCBI Taxonomy.")
    
    parser_refseqs.add_argument("refdir", 
            help="Path to NCBI RefSeq dir with sequences in FASTA format (*.fna). Walks subfolders.")
    parser_refseqs.add_argument("gi_taxid_dmp",
            help="Path to NCBI Taxonomy's 'gi_taxid_nucl.dmp'.")
    parser_refseqs.add_argument("--gi-taxid-db", dest="sqlite3db", type=existing_file,
            help="Specify a premade sqlite3 database with a gi_taxid(gi int, taxid int) table.")
    parser_refseqs.add_argument("--globpattern-fasta", dest="globpattern_fasta", type=str, metavar="'GLOB'",
            default="*.fna",
            help="Glob pattern for identifying FASTA files [%(default)s].")
    parser_refseqs.add_argument("-o", "--outfile", dest="outfile", metavar="FILE",
            default="header_taxid_mappings.tab",
            help="Output filename for header->taxid mappings [%(default)s].")
    parser_refseqs.add_argument("--loglevel", choices=["INFO", "DEBUG"], 
            default="DEBUG", 
            help="Set logging level [%(default)s].")
    parser_refseqs.add_argument("--logfile", 
            default=False,
            help="Log to file instead of STDOUT.")

    parser_proteodb.add_argument("header_mappings", 
            help="Two column text file with header->taxid mappings")
    parser_proteodb.add_argument("gene_info",
            help="Path to tab separated NCBI gene_info file.")
    parser_proteodb.add_argument("annotations_dir",
            help="Path to directory containing annotation files (*.gff).")
    parser_proteodb.add_argument("--dbfile", type=str, dest="dbfile",
            default="proteodb.sql", 
            help="Filename to write the proteotyping database to [%(default)s].")
    parser_proteodb.add_argument("--globpattern-gff", dest="globpattern_gff", type=str, metavar="'GLOB'",
            default="*.gff",
            help="Globpattern to find GFF files with in the annotations_dir.")
    parser_proteodb.add_argument("--taxonomy-ver", dest="taxonomy_ver", type=str,
            default="",
            help="Specify Taxonomy version, e.g. '2015-11-15'.")
    parser_proteodb.add_argument("--refseq-ver", dest="refseq_ver", type=str,
            default="",
            help="Specify RefSeq version, e.g. '2015-11-15'.")
    parser_proteodb.add_argument("--db-comment", dest="comment", type=str,
            default="",
            help="A database creation comment added to the SQLite3 database.")
    parser_proteodb.add_argument("--loglevel", choices=["INFO", "DEBUG"], 
            default="DEBUG", 
            help="Set logging level [%(default)s].")
    parser_proteodb.add_argument("--logfile", 
            default=False,
            help="Log to file instead of STDOUT.")

    if len(argv) < 2:
        parser.print_help()
        exit()

    options = parser.parse_args()
    
    if options.logfile:
        logging.basicConfig(level=options.loglevel, filename=options.logfile)
    else:
        logging.basicConfig(level=options.loglevel)

    return options


if __name__ == "__main__":

    options = parse_commandline(argv)

    if options.subcommand == "header_mappings":
        create_header_taxid_file(options.sqlite3db, 
                options.refdir, 
                options.gi_taxid_dmp, 
                options.outfile)
    elif options.subcommand == "proteodb":
        prepare_db(options.dbfile, 
                options.header_mappings, 
                options.gene_info,
                options.annotations_dir,
                options.globpattern_gff,
                options.taxonomy_ver, 
                options.refseq_ver, 
                options.comment)

