TCUP: Typing and Characterization using Proteomics
==================================================
TCUP uses peptides generated by mass spectrometry proteomics to:

* identify and estimate the taxonomic composition of a microbial sample.
* detect expressed antibiotic resistance proteins.

TCUP does not require a priori information about the contents of a sample and
is suitable for analysis of both pure cultures, mixed samples, and clinical
samples.  Have a look in the online `documentation`_ to learn how to use
TCUP.

.. _documentation: https://tcup.readthedocs.org


About
*****
:Authors: Fredrik Boulund
:Contact: fredrik.boulund@chalmers.se
:License: BSD


Installation 
************
Detailed installation instructions are available in the online
`documentation`_.

Download and install Anaconda Python 3.5 and create a conda environment
using the following commands::

    $ wget https://bitbucket.org/chalmersmathbioinformatics/tcup/conda_environment.yml
    $ conda env create -f conda_environment.yml

This will create a conda environment called ``tcup`` that contains 
all the required dependencies, and the ``tcup`` package itself. 

Dependencies
------------
TCUP depends on the following Python packages, easily installable via
``conda`` and ``pip``.

 * `ETE Toolkit`_ (ete3)
 * `XlsxWriter`_ (xlsxwriter)

.. _XlsxWriter: http://xlsxwriter.readthedocs.org/
.. _ETE Toolkit: http://etetoolkit.org/

Running
*******
In order to run TCUP, some databases need to be prepared. Please consult the
online `documentation`_ for details on how to build these.

The input for TCUP is mappings of peptides to reference genomes in blast8
tabular format. We recommend using `BLAT`_ or `pBLAT`_ for mapping.

.. _BLAT: https://genome.ucsc.edu/FAQ/FAQblat.html
.. _pBLAT: http://icebert.github.io/pblat/
